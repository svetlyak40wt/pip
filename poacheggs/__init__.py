"""hacked out of workingenv's underbelly"""
from optparse import OptionParser
import sys, os
import pkg_resources
import ConfigParser
import workingenv

my_package = pkg_resources.get_distribution('poacheggs')

def main(args=None):
    if args is None:
        args = sys.argv[1:]
    options, args = parser.parse_args(args)
    if not args or len(args) > 1:
        raise workingenv.BadCommand("You must provide at least one url or file to find install requirements")
    level = 1 # Notify
    level += options.verbose
    level -= options.quiet
    if options.simulate and not options.verbose:
        level += 1
    level = workingenv.Logger.level_for_integer(3-level)
    logger = workingenv.Logger([(level, sys.stdout)])
    output_dir = os.environ.get('WORKING_ENV', False)
    if not output_dir:
        die("you must be in an activated workingenv")
    logger.info('Reading settings from environment')
    settings = workingenv.Settings.read(output_dir)
    if settings.install_as_home:
        python_dir = os.path.join('lib', 'python')
    else:
        python_dir = os.path.join('lib', 'python%s' % workingenv.python_version)
        
    writer = workingenv.Writer(output_dir, logger, simulate=options.simulate,
                    interactive=options.interactive,
                    python_dir=python_dir)
    requirements = args
    requirement_lines = workingenv.read_requirements(logger, requirements)

    plan = workingenv.parse_requirements(logger, requirement_lines, settings)
    if settings.find_links:
        add_findlinks(settings, python_dir)
    if options.confirm:
        workingenv.check_requirements(writer, logger, plan)
    if plan:
        workingenv.install_requirements(writer, logger, plan)


def add_findlinks(settings, python_dir):
    dist_cp = ConfigParser.ConfigParser()
    cfg_name = os.path.join(python_dir, 'distutils', 'distutils.cfg')
    dist_cp.read(cfg_name)
    dist_cp.set('easy_install', 'find_links', ' '.join(settings.find_links))
    cfg = open(cfg_name, 'w')
    dist_cp.write(cfg)
    cfg.close()
        

help = "A list of files or URLs listing requirements that should be installed in the new environment (one requirement per line, optionally with -e for editable packages).  This file can also contain lines starting with -Z, -f, and -r"

parser = OptionParser(version=str(my_package),
                      usage="%%prog [OPTIONS] file_or_url\n\n%s" % help)

parser.add_option('-f', '--find-links',
                  action="append",
                  dest="find_links",
                  default=[],
                  metavar="URL",
                  help="Extra locations/URLs where packages can be found (sets up your distutils.cfg for future installs)")

parser.add_option('--force',
                  action="store_false",
                  dest="interactive",
                  default=True,
                  help="Overwrite files without asking")

parser.add_option('-n', '--simulate',
                  action="store_true",
                  dest="simulate",
                  help="Simulate (just pretend to do things)")

parser.add_option('--confirm',
                  dest='confirm',
                  action='store_true',
                  help="Confirm that the requirements have been installed, but don't do anything else (don't set up environment, don't install packages)")

parser.add_option('-v', '--verbose',
                  action="count",
                  dest="verbose",
                  default=0,
                  help="Be verbose (use multiple times for more effect)")

parser.add_option('-q', '--quiet',
                  action="count",
                  dest="quiet",
                  default=0,
                  help="Be more and more quiet")

from optparse import OptionParser
import sys, os
import pkg_resources
import logging
import re
import urlparse
import urllib2
import subprocess

my_package = pkg_resources.get_distribution('poacheggs')

def main(args=None):
    global logger
    if args is None:
        args = sys.argv[1:]
    options, args = parser.parse_args(args)
    if options.distutils_cfg:
        if args or options.requirements or options.editable:
            print 'If you use --distutils-cfg you cannot install any packages'
            sys.exit(2)
    elif not args and not options.requirements and not options.editable:
        print 'You must provide at least one url or file to find install requirements'
        sys.exit(2)
    level = 1 # Notify
    level += options.verbose
    level -= options.quiet
    level = Logger.level_for_integer(3-level)
    logger = Logger([(level, sys.stdout)])
    if options.distutils_cfg:
        main_distutils_cfg(options.distutils_cfg)
        return

    settings = dict(find_links=options.find_links, always_unzip=False)

    requirement_lines = read_requirements(logger, options.requirements)
    requirement_lines[:0] = [(a, '.') for a in args]
    for option in options.editable:
        requirement_lines.append(('-e %s' % option, '.'))
    logger.debug('Complete requirements:\n%s' % '\n'.join([
        '%s (from %s)' % (req, f)
        for req, f in requirement_lines]))

    plan = parse_requirements(logger, requirement_lines, settings)
    if options.confirm:
        check_requirements(logger, plan)
    elif plan:
        
        if options.src:
            src = options.src
        else:     # determine src directory
            for i in 'VIRTUAL_ENV', 'WORKING_ENV':
                if os.environ.has_key(i):
                    directory = os.environ[i]
                    break
            else:
                directory = '.'
            src = os.path.join(directory, 'src')            

        install_requirements(logger, plan, src, settings['find_links'])
    else:
        logger.notify('Nothing to install')

help = """\
A list of files or URLs that should be installed in the new
environment, and/or '-r REQUIREMENT_FILE' for lists of requirements.

A requirement with -e will install the requirement as 'editable'
(source unpacked and install in develop mode).

In a list of requirements: one requirement per line, optionally with
-e for editable packages).  This file can also contain lines starting
with -Z, -f, and -r; -Z to always unzip, -f to add to --find-links, -r
to reference another requirements file."""

parser = OptionParser(version=str(my_package),
                      usage="%%prog [OPTIONS] [REQUIREMENT...]\n\n%s" % help)

parser.add_option('-e', '--editable',
                  action="append",
                  dest="editable",
                  default=[],
                  metavar="REQUIREMENT",
                  help="Install this package as editable")

parser.add_option('-r', '--requirement',
                  action='append',
                  dest='requirements',
                  default=[],
                  metavar="REQUIREMENT_FILE",
                  help="Install requirements listed in the file")

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

parser.add_option('--src',
                  action='store',
                  metavar="SRC_DIR",
                  dest='src',
                  help="Directory to install source/editable packages into (default $VIRTUAL_ENV | $WORKING_ENV | ./src/)")

parser.add_option('--distutils-cfg',
                  action='append',
                  metavar='SECTION:OPTION:VALUE',
                  dest='distutils_cfg',
                  help='Update a setting in distutils.cfg, for example, --distutils-cfg=easy_install:index_url:http://download.zope.org/ppix/; '
                  'this option is exclusive of all other options.')


def main_distutils_cfg(new_options):
    new_settings = []
    for new_option in new_options:
        try:
            section, name, value = new_option.split(':', 2)
            if name.startswith('+'):
                append = True
                name = name[1:]
            else:
                append = False
        except ValueError:
            print 'Bad option: --distutils-cfg=%s' % new_option
            sys.exit(2)
        new_settings.append((section, name, value, append))
    distutils_file = find_distutils_file()
    for section, name, value, append in new_settings:
        update_distutils_file(distutils_file, section, name, value, append)
    logger.info('Updated %s' % distutils_file)

def find_distutils_file():
    import distutils.dist
    dist = distutils.dist.Distribution(None)
    files = dist.find_config_files()
    writable_files = []
    for file in files:
        if not os.path.exists(file):
            logger.info('Distutils config file %s does not exist' % file)
            continue
        if os.access(file, os.W_OK):
            logger.debug('Distutils config %s is writable' % file)
            writable_files.append(file)
        else:
            logger.notify('Distutils config %s is not writable' % file)
    if not files:
        logger.fatal(
            'Could not find any existing writable config file (tried options %s)'
            % ', '.join(files))
        raise OSError("No config files found")
    if len(files) > 1:
        logger.notify(
            "Choosing file %s among writable options %s"
            % (files[0], ', '.join(files[1:])))
    return files[0]

def update_distutils_file(filename, section, name, value, append):
    f = open(filename, 'r')
    lines = f.readlines()
    f.close()
    section_index = None
    for index, line in enumerate(lines):
        if line.strip().startswith('[%s]' % section):
            section_index = index
            break
    if section_index is None:
        logger.info('Adding section [%s]' % section)
        lines.append('[%s]\n' % section)
        lines.append('%s = %s\n' % (name, value))
    else:
        start_item_index = None
        item_index = None
        name_regex = re.compile(r'^%s\s*[=:]' % re.escape(name))
        whitespace_regex = re.compile(r'^\s+')
        for index_offset, line in enumerate(lines[section_index+1:]):
            index = index_offset + section_index + 1
            if item_index is not None:
                if whitespace_regex.match(line):
                    # continuation; point to last line
                    item_index = index
                else:
                    break
            if name_regex.match(line):
                start_item_index = item_index = index
            if line.startswith('['):
                # new section
                break
        if item_index is None:
            logger.info('Added %s to section [%s]' % (name, section))
            lines.insert(section_index+1,
                         '%s = %s\n' % (name, value))
        elif append:
            logger.info('Appended value %s to setting %s' % (value, name))
            lines.insert(item_index+1,
                         '    %s\n' % value)
        else:
            logger.info('Replaced setting %s' % name)
            lines[start_item_index:item_index+1] = ['%s = %s\n' % (name, value)]
        

############################################################
## Infrastructure


class Logger(object):

    """
    Logging object for use in command-line script.  Allows ranges of
    levels, to avoid some redundancy of displayed information.
    """

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    NOTIFY = (logging.INFO+logging.WARN)/2
    WARN = WARNING = logging.WARN
    ERROR = logging.ERROR
    FATAL = logging.FATAL

    LEVELS = [DEBUG, INFO, NOTIFY, WARN, ERROR, FATAL]

    def __init__(self, consumers):
        self.consumers = consumers
        self.indent = 0
        self.in_progress = None
        self.in_progress_hanging = False

    def debug(self, msg, *args, **kw):
        self.log(self.DEBUG, msg, *args, **kw)
    def info(self, msg, *args, **kw):
        self.log(self.INFO, msg, *args, **kw)
    def notify(self, msg, *args, **kw):
        self.log(self.NOTIFY, msg, *args, **kw)
    def warn(self, msg, *args, **kw):
        self.log(self.WARN, msg, *args, **kw)
    def error(self, msg, *args, **kw):
        self.log(self.WARN, msg, *args, **kw)
    def fatal(self, msg, *args, **kw):
        self.log(self.FATAL, msg, *args, **kw)
    def log(self, level, msg, *args, **kw):
        if args:
            if kw:
                raise TypeError(
                    "You may give positional or keyword arguments, not both")
        args = args or kw
        rendered = None
        for consumer_level, consumer in self.consumers:
            if self.level_matches(level, consumer_level):
                if (self.in_progress_hanging
                    and consumer in (sys.stdout, sys.stderr)):
                    self.in_progress_hanging = False
                    sys.stdout.write('\n')
                    sys.stdout.flush()
                if rendered is None:
                    if args:
                        rendered = msg % args
                    else:
                        rendered = msg
                    rendered = ' '*self.indent + rendered
                if hasattr(consumer, 'write'):
                    consumer.write(rendered+'\n')
                else:
                    consumer(rendered)

    def start_progress(self, msg):
        assert not self.in_progress, (
            "Tried to start_progress(%r) while in_progress %r"
            % (msg, self.in_progress))
        if self.level_matches(self.NOTIFY, self._stdout_level()):
            sys.stdout.write(msg)
            sys.stdout.flush()
            self.in_progress_hanging = True
        else:
            self.in_progress_hanging = False
        self.in_progress = msg

    def end_progress(self, msg='done.'):
        assert self.in_progress, (
            "Tried to end_progress without start_progress")
        if self.stdout_level_matches(self.NOTIFY):
            if not self.in_progress_hanging:
                # Some message has been printed out since start_progress
                sys.stdout.write('...' + self.in_progress + msg + '\n')
                sys.stdout.flush()
            else:
                sys.stdout.write(msg + '\n')
                sys.stdout.flush()
        self.in_progress = None
        self.in_progress_hanging = False

    def show_progress(self):
        """If we are in a progress scope, and no log messages have been
        shown, write out another '.'"""
        if self.in_progress_hanging:
            sys.stdout.write('.')
            sys.stdout.flush()

    def stdout_level_matches(self, level):
        """Returns true if a message at this level will go to stdout"""
        return self.level_matches(level, self._stdout_level())

    def _stdout_level(self):
        """Returns the level that stdout runs at"""
        for level, consumer in self.consumers:
            if consumer is sys.stdout:
                return level
        return self.FATAL

    def level_matches(self, level, consumer_level):
        """
        >>> l = Logger()
        >>> l.level_matches(3, 4)
        False
        >>> l.level_matches(3, 2)
        True
        >>> l.level_matches(slice(None, 3), 3)
        False
        >>> l.level_matches(slice(None, 3), 2)
        True
        >>> l.level_matches(slice(1, 3), 1)
        True
        >>> l.level_matches(slice(2, 3), 1)
        False
        """
        if isinstance(level, slice):
            start, stop = level.start, level.stop
            if start is not None and start > consumer_level:
                return False
            if stop is not None or stop <= consumer_level:
                return False
            return True
        else:
            return level >= consumer_level

    #@classmethod
    def level_for_integer(cls, level):
        levels = cls.LEVELS
        if level < 0:
            return levels[0]
        if level >= len(levels):
            return levels[-1]
        return levels[level]

    level_for_integer = classmethod(level_for_integer)

def read_requirements(logger, requirements):
    """
    Read all the lines from the requirement files, including recursive
    reads.
    """
    lines = []
    req_re = re.compile(r'^(?:-r|--requirements)\s+')
    for fn in requirements:
        logger.info('Reading requirement %s' % fn)
        for line in get_lines(fn):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            match = req_re.search(line)
            if match:
                sub_fn = line[match.end():]
                sub_fn = join_filename(fn, sub_fn)
                lines.extend(read_requirements(logger, [sub_fn]))
                continue
            lines.append((line, fn))
    return lines

def parse_requirements(logger, requirement_lines, settings):
    """
    Parse all the lines of requirements.  Can override options.
    Returns a list of requirements to be installed.
    """
    options_re = re.compile(r'^--?([a-zA-Z0-9_-]*)\s+')
    plan = []
    for line, uri in requirement_lines:
        match = options_re.search(line)
        if match:
            option = match.group(1)
            value = line[match.end():]
            if option in ('f', 'find-links'):
                value = join_filename(uri, value)
                if value not in settings['find_links']:
                    settings['find_links'].append(value)
            elif option in ('Z', 'always-unzip'):
                settings['always_unzip'] = True
            elif option in ('e', 'editable'):
                plan.append(
                    ('--editable', join_filename(uri, value, only_req_uri=True)))
            else:
                logger.error("Bad option override in requirement: %s" % line)
            continue
        plan.append(join_filename(uri, line, only_req_uri=True))
    return plan

def check_requirements(logger, plan):
    """
    Check all the requirements found in the list of filenames
    """
    import pkg_resources
    for req in plan:
        if isinstance(req, tuple) and req[0] == '--editable':
            req = req[1]
        if '#egg=' in req:
            req = req.split('#egg=')[-1]
        try:
            dist = pkg_resources.get_distribution(req)
            logger.notify("Found: %s" % dist)
            logger.info("  in location: %s" % dist.location)
        except pkg_resources.DistributionNotFound:
            logger.warn("Not Found: %s" % req)
        except pkg_resources.VersionConflict, e:
            logger.warn("Conflict for requirement %s" % req)
            logger.warn("  %s" % e)
        except ValueError, e:
            logger.warn("Cannot confirm %s" % req)

def install_requirements(logger, plan, src_path, find_links):
    """
    Install all the requirements found in the list of filenames
    """
    import pkg_resources
    immediate = []
    editable = []
    for req in plan:
        if req[0] == '--editable':
            editable.append(req[1])
        else:
            immediate.append(req)
    find_link_ops = []
    for find_link in find_links:
        find_link_ops.extend(['-f', find_link])
    if immediate:
        args = _quote_args(find_link_ops + immediate)
        logger.start_progress('Installing %s\nInstalling ...' % ', '.join(immediate))
        logger.indent += 2
        call_subprocess(
            [sys.executable, '-c',
             "import setuptools.command.easy_install; "
             "setuptools.command.easy_install.main([\"-q\", %s])"
             % args], logger,
            show_stdout=False,
            filter_stdout=make_filter_easy_install())
        logger.indent -= 2
        logger.end_progress()
    src_path = os.path.abspath(src_path)
    prev_path = os.getcwd()
    try:
        for req in editable:
            if not os.path.exists(src_path):
                logger.notify('Creating directory %s' % src_path)
                os.makedirs(src_path)
            logger.debug('Changing to directory %s' % src_path)
            os.chdir(src_path)
            req = req.replace('"', '').replace("'", '')
            dist_req = pkg_resources.Requirement.parse(req)
            dir = os.path.join(src_path, dist_req.project_name.lower())
            dir_exists = os.path.exists(dir)
            if dir_exists:
                logger.info('Package %s already installed in editable form'
                            % req)
            else:
                logger.start_progress('Installing editable %s to %s...' % (req, dir))
                logger.indent += 2
                args = _quote_args(find_link_ops + [req])
                cmd = [sys.executable, '-c',
                       "import setuptools.command.easy_install; "
                       "setuptools.command.easy_install.main("
                       "[\"-q\", \"-b\", \".\", \"-e\", %s])"
                       % args]
                call_subprocess(
                    cmd, logger,
                    show_stdout=False, filter_stdout=make_filter_easy_install())
            cur_dir = os.getcwd()
            os.chdir(dir)
            call_subprocess(
                [sys.executable, '-c',
                 "import setuptools; execfile(\"setup.py\")",
                 "develop"], logger,
                show_stdout=False, filter_stdout=make_filter_develop())
            if not dir_exists:
                logger.indent -= 2
                logger.end_progress()
    finally:
        os.chdir(prev_path)

def _quote_args(args):
    return ', '.join([
        '"%s"' % arg.replace('"', '').replace("'", '') for arg in args])

def get_lines(fn_or_url):
    scheme = urlparse.urlparse(fn_or_url)[0]
    if not scheme:
        # Must be filename
        f = open(fn_or_url)
    else:
        f = urllib2.urlopen(fn_or_url)
    try:
        return f.readlines()
    finally:
        f.close()

def join_filename(base, sub, only_req_uri=False):
    if only_req_uri and '#' not in sub:
        return sub
    if re.search(r'^https?://', base) or re.search(r'^https?://', sub):
        return urlparse.urljoin(base, sub)
    else:
        base = os.path.dirname(os.path.abspath(base))
        return os.path.join(base, sub)

def call_subprocess(cmd, logger, show_stdout=True,
                    filter_stdout=None, cwd=None,
                    raise_on_returncode=True):
    cmd_parts = []
    for part in cmd:
        if ' ' in part or '\n' in part or '"' in part or "'" in part:
            part = '"%s"' % part.replace('"', '\\"')
        cmd_parts.append(part)
    cmd_desc = ' '.join(cmd_parts)
    if show_stdout:
        stdout = None
    else:
        stdout = subprocess.PIPE
    logger.debug("Running command %s" % cmd_desc)
    try:
        proc = subprocess.Popen(
            cmd, stderr=subprocess.STDOUT, stdin=None, stdout=stdout,
            cwd=cwd)
    except Exception, e:
        logger.fatal(
            "Error %s while executing command %s" % (e, cmd_desc))
        raise
    all_output = []
    if stdout is not None:
        stdout = proc.stdout
        while 1:
            line = stdout.readline()
            if not line:
                break
            line = line.rstrip()
            all_output.append(line)
            if filter_stdout:
                level = filter_stdout(line)
                if isinstance(level, tuple):
                    level, line = level
                logger.log(level, line)
                if not logger.stdout_level_matches(level):
                    logger.show_progress()
            else:
                logger.info(line)
    else:
        proc.communicate()
    proc.wait()
    if proc.returncode:
        if raise_on_returncode:
            if all_output:
                logger.notify('Complete output from command %s:' % cmd_desc)
                logger.notify('\n'.join(all_output) + '\n----------------------------------------')
            raise OSError(
                "Command %s failed with error code %s"
                % (cmd_desc, proc.returncode))
        else:
            logger.warn(
                "Command %s had error code %s"
                % (cmd_desc, proc.returncode))

## Filters for call_subprocess:

def make_filter_easy_install():
    context = []
    def filter_easy_install(line):
        adjust = 0
        level = Logger.NOTIFY
        prefix = 'Processing dependencies for '
        if line.startswith(prefix):
            requirement = line[len(prefix):].strip()
            context.append(requirement)
            adjust = -2
        prefix = 'Finished installing '
        if line.startswith(prefix):
            requirement = line[len(prefix):].strip()
            if not context or context[-1] != requirement:
                # For some reason the top-level context is often None from
                # easy_install.process_distribution; so we shouldn't worry
                # about inconsistency in that case
                if len(context) != 1 or requirement != 'None':
                    print 'Error: Got unexpected "%s%s"' % (prefix, requirement)
                    print '       Context: %s' % context
            context.pop()
        if not line.strip():
            level = Logger.DEBUG
        for regex in [r'references __(file|path)__$',
                      r'^zip_safe flag not set; analyzing',
                      r'MAY be using inspect.[a-zA-Z0-9_]+$',
                      r'^Extracting .*to',
                      r'^creating .*\.egg$',
                      ]:
            if re.search(regex, line.strip()):
                level = Logger.DEBUG
        indent = len(context)*2 + adjust
        return (level, ' '*indent + line)
    return filter_easy_install


def make_filter_develop():
    easy_filter = make_filter_easy_install()
    def filter_develop(line):
        for regex in [r'^writing.*egg-info']:
            if re.search(regex, line.strip()):
                return Logger.DEBUG
        return easy_filter(line)
    return filter_develop

from optparse import OptionParser
import sys, os
import pkg_resources
import logging
import re
import urlparse
import urllib2
import subprocess

my_package = pkg_resources.get_distribution('poacheggs')

class BadCommand(Exception):
    """
    Raised when the command is improperly invoked
    """

    def catcher(cls, func):
        def replacement(args=None):
            if args is None:
                args = sys.argv[1:]
            try:
                return func(args)
            except cls, e:
                print str(e)
                sys.exit(2)
        return replacement
    catcher = classmethod(catcher)

@BadCommand.catcher
def main(args):
    global logger
    options, args = parser.parse_args(args)
    if options.distutils_cfg:
        if args or options.requirements or options.editable:
            raise BadCommand('If you use --distutils-cfg you cannot install any packages')
    elif options.freeze_filename:
        if args or options.requirements or options.editable:
            raise BadCommand('If you use --freeze you cannot install any packages')
    elif not args and not options.requirements and not options.editable:
        raise BadCommand('You must provide at least one url or file to find install requirements')
    level = 1 # Notify
    level += options.verbose
    level -= options.quiet
    level = Logger.level_for_integer(3-level)
    logger = Logger([(level, sys.stdout)])
    if not options.src:
        for i in 'VIRTUAL_ENV', 'WORKING_ENV':
            if os.environ.has_key(i):
                directory = os.environ[i]
                break
        else:
            directory = '.'
        options.src = os.path.join(directory, 'src')
    options.src = os.path.expanduser(options.src)
    if options.distutils_cfg:
        main_distutils_cfg(options.distutils_cfg)
        return

    if options.freeze_filename:
        srcs = [options.src] + options.find_src
        srcs = [dir for dir in srcs if dir]
        main_freeze(options.freeze_filename, srcs, options.freeze_find_tags)
        warn_global_eggs()
        return

    warn_global_eggs()

    settings = dict(find_links=options.find_links, always_unzip=False,
                    egg_cache=options.egg_cache, variables={})

    requirement_lines = read_requirements(logger, options.requirements)
    requirement_lines[:0] = [(a, '.') for a in args]
    for option in options.editable:
        requirement_lines.append(('-e %s' % option, '.'))
    logger.debug('Complete requirements:\n%s' % '\n'.join([
        '%s (from %s)' % (req, f)
        for req, f in requirement_lines]))

    plan = parse_requirements(logger, requirement_lines, settings)
    if options.confirm:
        check_requirements(logger, plan,
                           settings, cache_only=options.cache_only)
    elif options.collect:
        if options.cache_only:
            raise BadCommand('--collect and --cache-only do not make sense to use together')
        if not options.egg_cache:
            raise BadCommand('If using --collect you must provide --egg-cache')
        if not os.path.exists(options.egg_cache):
            logger.notify('Creating egg cache directory %s' % options.egg_cache)
            os.makedirs(options.egg_cache)
        install_requirements(logger, plan, src_path=None, find_links=settings['find_links'],
                             cache_only=False, fetch_only=True,
                             egg_cache=options.egg_cache)
    elif plan:
        if options.egg_cache:
            settings['find_links'].append(make_file_url(options.egg_cache))
        install_requirements(logger, plan, options.src, settings['find_links'],
                             cache_only=options.cache_only)
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
to reference another requirements file.

If you use --freeze then the requirements file will be overwritten
with the exact packages currently installed.
"""

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

parser.add_option('--egg-cache',
                  dest='egg_cache',
                  metavar='DIR',
                  help='A directory where a cache of eggs is found (or if you use --collect, where they should be placed)')

parser.add_option('--collect',
                  dest='collect',
                  action='store_true',
                  help='Collect the eggs for this installation, but do not install them')

parser.add_option('--cache-only',
                  dest='cache_only',
                  action='store_true',
                  help='Get eggs from the cache only (do not look on the network)')

parser.add_option('--find-src',
                  dest='find_src',
                  metavar='DIR',
                  action='append',
                  default=[],
                  help='A directory where source checkouts can be found')

parser.add_option('--freeze',
                  dest='freeze_filename',
                  metavar='FILENAME',
                  help='Freeze the currently-installed packages into a new requirements file FILENAME (use - for stdout)')

parser.add_option('--freeze-find-tags',
                  dest='freeze_find_tags',
                  action='store_true',
                  help='If freezing a trunk, see if there\'s a workable tag (can be slow)')

def warn_global_eggs():
    if hasattr(sys, 'real_prefix'):
        # virtualenv
        ## FIXME: this isn't right on Windows
        check_prefix = os.path.join(sys.real_prefix, 'lib', 'python'+sys.version[:3])
    elif os.environ.get('WORKING_ENV'):
        # workingenv
        check_prefix = os.path.join(sys.prefix, 'lib', 'python'+sys.version[:3])
    else:
        # normal global environ, no need to warn
        return
    for path in sys.path:
        if not path.endswith('.egg'):
            continue
        if os.path.basename(path).startswith('setuptools'):
            # This is okay.
            continue
        if path.startswith(check_prefix):
            logger.notify(
                "global eggs may cause problems: %s" % path)

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
    f = open(filename, 'w')
    f.writelines(lines)
    f.close()

############################################################
## Freezing

rev_re = re.compile(r'-r(\d+)$')

def main_freeze(freeze_filename, srcs, find_tags):
    if freeze_filename == '-':
        logger.move_stdout_to_stderr()
    settings = dict(find_links=[], always_unzip=False,
                    egg_cache=None, variables={})
    dependency_links = []
    if os.path.exists(freeze_filename):
        logger.notify('Reading settings from %s' % freeze_filename)
        lines = read_requirements(logger, [freeze_filename])
        plan = parse_requirements(logger, lines, settings)
        for item in plan:
            if isinstance(item, tuple):
                assert item[0] == '--editable'
                continue
            if item.startswith('http:') or item.startswith('https:'):
                dependency_links.append(item)
    if freeze_filename == '-':
        f = sys.stdout
    else:
        f = open(freeze_filename, 'w')
    srcs = [os.path.normcase(os.path.abspath(os.path.expanduser(src))) for src in srcs]
    for src in srcs:
        if not os.path.exists(src):
            logger.warn('src directory %s does not exist' % src)
    for dist in pkg_resources.working_set:
        if dist.has_metadata('dependency_links.txt'):
            dependency_links.extend(dist.get_metadata_lines('dependency_links.txt'))
    for link in settings['find_links']:
        if '#egg' in link:
            dependency_links.append(link)
    for link in sorted(settings['find_links']):
        print >> f, '-f %s' % link
    if settings['always_unzip']:
        print >> f, '--always-unzip'
    for setting_name, setting_value in sorted(settings['variables'].items()):
        print >> f, format_setting(setting_name, setting_value)
    packages = sorted(pkg_resources.working_set, key=lambda d: d.project_name)
    for dist in packages:
        if dist.key == 'setuptools' or dist.key == 'poacheggs':
            ## FIXME: also skip virtualenv?
            continue
        location = os.path.normcase(os.path.abspath(dist.location))
        if os.path.exists(os.path.join(location, '.svn')):
            for src in srcs:
                if location.startswith(src):
                    break
            else:
                logger.warn('Warning: svn checkout not in any src (%s): %s' % (', '.join(srcs), location))
            req = get_src_requirement(dist, location, find_tags)
        else:
            req = dist.as_requirement()
            specs = req.specs
            assert len(specs) == 1 and specs[0][0] == '=='
            version = specs[0][1]
            match = rev_re.search(version)
            if match:
                svn_location = get_svn_location(dist, dependency_links)
                if not svn_location:
                    logger.warn(
                        'Warning: cannot find svn location for %s' % req)
                    print >> f, '# could not find svn URL in dependency_links for any package'
                else:
                    print >> f, '# installing editable to satisfy requirement %s' % req
                    req = '-e %s@%s' % (svn_location, match.group(1))
        print >> f, req
    if freeze_filename != '-':
        logger.notify('Put requirements in %s' % freeze_filename)
        f.close()

def format_setting(name, value):
    lines = value.splitlines()
    result = '%s = %s' % (name, lines[0])
    # Lines up following ilnes with the first line value:
    padding = ' '*(len(name)+3)
    for line in lines[1:]:
        result += '\n%s%s' % (padding, line)
    return result

egg_fragment_re = re.compile(r'#egg=(.*)$')

def get_svn_location(dist, dependency_links):
    keys = []
    for link in dependency_links:
        match = egg_fragment_re.search(link)
        if not match:
            continue
        name = match.group(1)
        if '-' in name:
            key = '-'.join(name.split('-')[:-1]).lower()
        else:
            key = name
        if key == dist.key:
            return link.split('#', 1)[0]
        keys.append(key)
    return None

def get_src_requirement(dist, location, find_tags):
    if not os.path.exists(os.path.join(location, '.svn')):
        logger.warn('cannot determine version of editable source in %s (is not svn checkout)' % location)
        return dist.as_requirement()
    repo = get_svn_url(location)
    parts = repo.split('/')
    if parts[-2] in ('tags', 'tag'):
        # It's a tag, perfect!
        return '-e %s#egg=%s-%s' % (repo, dist.project_name, parts[-1])
    elif parts[-2] in ('branches', 'branch'):
        # It's a branch :(
        rev = get_svn_revision(location)
        return '-e %s@%s#egg=%s-%s%s-r%s' % (repo, rev, dist.project_name, dist.version, parts[-1], rev)
    elif parts[-1] == 'trunk':
        # Trunk :-/
        rev = get_svn_revision(location)
        if find_tags:
            tag_url = '/'.join(parts[:-1]) + '/tags'
            tag_revs = get_tag_revs(tag_url)
            match = find_tag_match(rev, tag_revs)
            if match:
                logger.notify('trunk checkout %s seems to be equivalent to tag %s' % match)
                return '-e %s/%s#egg=%s-%s' % (tag_url, match, dist.project_name, match)
        return '-e %s@%s#egg=%s-dev' % (repo, rev, dist.project_name)
    else:
        # Don't know what it is
        logger.warn('svn URL does not fit normal structure (tags/branches/trunk): %s' % repo)
        rev = get_svn_revision(location)
        return '-e %s@%s#egg=%s-dev' % (repo, rev, dist.project_name)

_svn_url_re = re.compile('url="([^"]+)"')
_svn_rev_re = re.compile('committed-rev="(\d+)"')

def get_svn_revision(location):
    """
    Return the maximum revision for all files under a given location
    """
    # Note: taken from setuptools.command.egg_info
    revision = 0

    for base, dirs, files in os.walk(location):
        if '.svn' not in dirs:
            dirs[:] = []
            continue    # no sense walking uncontrolled subdirs
        dirs.remove('.svn')
        entries_fn = os.path.join(base, '.svn', 'entries')
        if not os.path.exists(entries_fn):
            ## FIXME: should we warn?
            continue
        f = open(entries_fn)
        data = f.read()
        f.close()

        if data.startswith('8'):
            data = map(str.splitlines,data.split('\n\x0c\n'))
            del data[0][0]  # get rid of the '8'
            dirurl = data[0][3]
            revs = [int(d[9]) for d in data if len(d)>9 and d[9]]+[0]
            if revs:
                localrev = max(revs)
            else:
                localrev = 0
        elif data.startswith('<?xml'):
            dirurl = _svn_url_re.search(data).group(1)    # get repository URL
            revs = [int(m.group(1)) for m in _svn_rev_re.finditer(data)]+[0]
            if revs:
                localrev = max(revs)
            else:
                localrev = 0
        else:
            logger.warn("unrecognized .svn/entries format; skipping %s", base)
            dirs[:] = []
            continue
        if base == location:
            base_url = dirurl+'/'   # save the root url
        elif not dirurl.startswith(base_url):
            dirs[:] = []
            continue    # not part of the same svn tree, skip it
        revision = max(revision, localrev)

    return revision

def get_svn_url(location):
    f = open(os.path.join(location, '.svn', 'entries'))
    data = f.read()
    f.close()
    if data.startswith('8'):
        data = map(str.splitlines,data.split('\n\x0c\n'))
        del data[0][0]  # get rid of the '8'
        return data[0][3]
    elif data.startswith('<?xml'):
        return _svn_url_re.search(data).group(1)    # get repository URL
    else:
        logger.warn("unrecognized .svn/entries format in %s" % location)
        # Or raise exception?
        return None

def get_tag_revs(svn_tag_url):
    stdout = call_subprocess(
        ['svn', 'ls', '-v', svn_tag_url], logger, show_stdout=False)
    results = []
    for line in stdout.splitlines():
        parts = line.split()
        rev = int(parts[0])
        tag = parts[-1].strip('/')
        results.append((tag, rev))
    return results

def find_tag_match(rev, tag_revs):
    best_match_rev = None
    best_tag = None
    for tag, tag_rev in tag_revs:
        if (tag_rev > rev and
            (best_match_rev is None or best_match_rev > tag_rev)):
            # FIXME: Is best_match > tag_rev really possible?
            # or is it a sign something is wacky?
            best_match_rev = tag_rev
            best_tag = tag
    return best_tag

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

    def move_stdout_to_stderr(self):
        to_remove = []
        to_add = []
        for consumer_level, consumer in self.consumers:
            if consumer == sys.stdout:
                to_remove.append((consumer_level, consumer))
                to_add.append((consumer_level, sys.stderr))
        for item in to_remove:
            self.consumers.remove(item)
        self.consumers.extend(to_add)

############################################################
## Requirement file parsing stuff

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
            line = line.rstrip()
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
    print 'lines:', requirement_lines
    options_re = re.compile(r'^--?([a-zA-Z0-9_-]+)\s*')
    setting_re = re.compile(r'^(\w+)\s*=\s*(.*)$')
    plan = []
    in_setting = None
    setting_variables = settings['variables']
    for line, uri in requirement_lines:
        line = line.rstrip()
        match = setting_re.search(line)
        if match:
            variable = match.group(1)
            setting_variables[variable] = match.group(2)
            in_setting = variable
            continue
        if line.strip() != line and in_setting:
            # Continuation line
            cur = setting_variables[in_setting]
            setting_variables[in_setting] = cur + '\n' + line.strip()
            continue
        in_settting = None
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
        
def install_requirements(logger, plan, src_path, find_links,
                         cache_only=False, fetch_only=False,
                         egg_cache=None):
    """
    Install all the requirements found in the list of filenames
    """
    immediate = []
    editable = []
    for req in plan:
        if req[0] == '--editable':
            editable.append(req[1])
        else:
            immediate.append(req)
    easy_ops = []
    for find_link in find_links:
        easy_ops.extend(['-f', find_link])
    if cache_only:
        # This kind of implicitly disables fetching over the network
        easy_ops.extend(['--allow-hosts', '^(localhost|.*\.local)$'])
    if fetch_only:
        assert egg_cache is not None
        easy_ops.extend(['--zip-ok', '--multi-version', '--install-dir', egg_cache])
        ## FIXME: it would be best to actually remove these eggs at the end:
        immediate.extend(editable)
        message = 'Fetching'
    else:
        message = 'Installing'
    if immediate:
        args = _quote_args(easy_ops + immediate)
        logger.start_progress('%s %s\n%s ...' % (message, ', '.join(immediate), message))
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
    if fetch_only:
        return
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
                args = _quote_args(easy_ops + [req])
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
                 "import setuptools; __file__=\"setup.py\"; execfile(\"setup.py\")",
                 "develop"], logger,
                show_stdout=False, filter_stdout=make_filter_develop())
            if not dir_exists:
                logger.indent -= 2
                logger.end_progress()
    finally:
        os.chdir(prev_path)

def make_file_url(path):
    path = os.path.abspath(path)
    path = path.replace(os.path.sep, '/')
    return 'file://'+path

############################################################
## Misc functions

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
        returned_stdout, returned_stderr = proc.communicate()
        all_output = [returned_stdout]
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
    if stdout is not None:
        return ''.join(all_output)

############################################################
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
                      #r'^Extracting .*to',
                      #r'^creating .*\.egg$',
                      r": top-level module may be 'python -m' script$",
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

#!/usr/bin/env python
import sys
import os
import optparse
import pkg_resources
import urllib2
import urllib
import mimetypes
import zipfile
import tarfile
import tempfile
import subprocess
import posixpath
import re
import shutil
try:
    from hashlib import md5
except ImportError:
    import md5
import urlparse
from email.FeedParser import FeedParser
import poacheggs
import traceback
from cStringIO import StringIO
import socket
from Queue import Queue
from Queue import Empty as QueueEmpty
import threading
import httplib

class InstallationError(Exception):
    """General exception during installation"""

class DistributionNotFound(InstallationError):
    """Raised when a distribution cannot be found to satisfy a requirement"""

if getattr(sys, 'real_prefix', None):
    ## FIXME: is src/ really good?  Should it be something to imply these are just installation files?
    base_prefix = os.path.join(sys.prefix, 'build')
    base_src_prefix = os.path.join(sys.prefix, 'src')
else:
    ## FIXME: this isn't a very good default
    base_prefix = os.path.join(os.getcwd(), 'build')
    base_prefix = os.path.join(os.getcwd(), 'src')

pypi_url = "http://pypi.python.org/simple"

parser = optparse.OptionParser(
    usage='%prog [OPTIONS] PACKAGE_NAMES')

parser.add_option(
    '-e', '--editable',
    dest='editables',
    action='append',
    default=[],
    metavar='svn+REPOS_URL[@REV]#egg=PACKAGE',
    help='Install a package directly from a checkout.  Source will be checked '
    'out into src/PACKAGE (lower-case) and installed in-place (using '
    'setup.py develop).  This option may be provided multiple times.')

parser.add_option(
    '-f', '--find-links',
    dest='find_links',
    action='append',
    default=[],
    metavar='URL',
    help='URL to look for packages at')
parser.add_option(
    '-i', '--index-url',
    dest='index_url',
    metavar='URL',
    default=pypi_url,
    help='base URL of Python Package Index')
parser.add_option(
    '--extra-index-url',
    dest='extra_index_urls',
    metavar='URL',
    action='append',
    default=[],
    help='extra URLs of package indexes to use in addition to --index-url')

parser.add_option(
    '-b', '--build-dir', '--build-directory',
    dest='build_dir',
    metavar='DIR',
    default=base_prefix,
    help='Unpack packages into DIR (default %s) and build from there' % base_prefix)
parser.add_option(
    '--src', '--source',
    dest='src_dir',
    metavar='DIR',
    default=base_src_prefix,
    help='Check out --editable packages into DIR (default %s)' % base_src_prefix)
parser.add_option(
    '--timeout',
    metavar='SECONDS',
    dest='timeout',
    type='int',
    default=10,
    help='Set the socket timeout (default 10 seconds)')

parser.add_option(
    '-U', '--upgrade',
    dest='upgrade',
    action='store_true',
    help='Upgrade all packages to the newest available version')
parser.add_option(
    '-I', '--ignore-installed',
    dest='ignore_installed',
    action='store_true',
    help='Ignore the installed packages (reinstalling instead)')
parser.add_option(
    '--no-install',
    dest='no_install',
    action='store_true',
    help="Download and unpack all packages, but don't actually install them")

parser.add_option(
    '-v', '--verbose',
    dest='verbose',
    action='count',
    default=0,
    help='Give more output')
parser.add_option(
    '-q', '--quiet',
    dest='quiet',
    action='count',
    default=0,
    help='Give less output')

def main(args=None):
    global logger
    if args is None:
        args = sys.argv[1:]
    options, args = parser.parse_args(args)

    level = 1 # Notify
    level += options.verbose
    level -= options.quiet
    level = poacheggs.Logger.level_for_integer(4-level)
    logger = poacheggs.Logger([(level, sys.stdout)])
    ## FIXME: this is hacky :(
    poacheggs.logger = logger
    socket.setdefaulttimeout(options.timeout or None)

    index_urls = [options.index_url] + options.extra_index_urls
    finder = PackageFinder(
        find_links=options.find_links,
        index_urls=index_urls)
    requirement_set = RequirementSet(build_dir=options.build_dir,
                                     src_dir=options.src_dir,
                                     upgrade=options.upgrade,
                                     ignore_installed=options.ignore_installed)
    for name in args:
        requirement_set.add_requirement(
            InstallRequirement(name, None))
    for name in options.editables:
        name, checkout_url = parse_editable(name)
        requirement_set.add_requirement(
            InstallRequirement(name, None, editable=True, checkout_url=checkout_url))
    try:
        requirement_set.install_files(finder)
        if not options.no_install:
            requirement_set.install()
            logger.notify('Successfully installed %s' % requirement_set)
        else:
            logger.notify('Successfully downloaded %s' % requirement_set)
    except InstallationError, e:
        logger.fatal(str(e))
        logger.info('Exception information:\n%s' % format_exc())
        sys.exit(1)
    except:
        logger.fatal('Exception:\n%s' % format_exc())
        sys.exit(2)

def format_exc(exc_info=None):
    if exc_info is None:
        exc_info = sys.exc_info()
    out = StringIO()
    traceback.print_exception(*exc_info, **dict(file=out))
    return out.getvalue()

class PackageFinder(object):
    """This finds packages.

    This is meant to match easy_install's technique for looking for
    packages, by reading pages and looking for appropriate links
    """

    failure_limit = 3

    def __init__(self, find_links, index_urls):
        self.find_links = find_links
        self.index_urls = index_urls
        self.dependency_links = []
        self.cache = PageCache()
    
    def add_dependency_links(self, links):
        ## FIXME: this shouldn't be global list this, it should only
        ## apply to requirements of the package that specifies the
        ## dependency_links value
        self.dependency_links.extend(links)

    def find_requirement(self, req, upgrade):
        url_name = req.url_name
        # Check that we have the url_name correctly spelled:
        main_index_url = Link(posixpath.join(self.index_urls[0], url_name))
        # This will also cache the page, so it's okay that we get it again later:
        page = self._get_page(main_index_url, req)
        if page is None:
            url_name = self._find_url_name(Link(self.index_urls[0]), url_name, req)
        locations = [
            posixpath.join(url, url_name)
            for url in self.index_urls] + self.find_links + self.dependency_links
        for version in req.absolute_versions:
            locations = [
                posixpath.join(url, url_name, version)] + locations
        locations = [Link(url) for url in locations]
        logger.debug('URLs to search for versions for %s:' % req)
        for location in locations:
            logger.debug('* %s' % location)
        found_versions = []
        for page in self._get_pages(locations, req):
            logger.debug('Analyzing links from page %s' % page.url)
            logger.indent += 2
            try:
                found_versions.extend(self._package_versions(page.links, req.name.lower()))
            finally:
                logger.indent -= 2
        if not found_versions:
            logger.fatal('Could not find any downloads that satisfy the requirement %s' % req)
            raise DistributionNotFound('No distributions at all found for %s' % req)
        if req.satisfied_by is not None:
            found_versions.append((req.satisfied_by.parsed_version, Inf, req.satisfied_by.version))
        found_versions.sort(reverse=True)
        applicable_versions = []
        for (parsed_version, link, version) in found_versions:
            if version not in req.req:
                logger.info("Removing link %s, version %s doesn't match %s"
                            % (link, version, ','.join([''.join(s) for s in req.req.specs])))
                continue
            applicable_versions.append((link, version))
        existing_applicable = bool([link for link, version in applicable_versions if link is Inf])
        if not upgrade and existing_applicable:
            if applicable_versions[0][1] is Inf:
                logger.info('Existing installed version (%s) is most up-to-date and satisfies requirement'
                            % req.satisfied_by.version)
            else:
                logger.info('Existing installed version (%s) satisfies requirement (most up-to-date version is %s)'
                            % (req.satisfied_by.version, application_versions[0][2]))
            return None
        if not applicable_versions:
            logger.fatal('Could not find a version that satisfies the requirement %s (from versions: %s)'
                         % (req, ', '.join([version for parsed_version, link, version in found_versions])))
            raise DistributionNotFound('No distributions matching the version for %s' % req)
        if applicable_versions[0][0] is Inf:
            # We have an existing version, and its the best version
            logger.info('Installed version (%s) is most up-to-date (past versions: %s)'
                        % (req.satisfied_by.version, ', '.join([version for link, version in applicable_versions[1:]]) or 'none'))
            return None
        if len(applicable_versions) > 1:
            logger.info('Using version %s (newest of versions: %s)' %
                        (applicable_versions[0][1], ', '.join([version for link, version in applicable_versions])))
        return applicable_versions[0][0]

    def _find_url_name(self, index_url, url_name, req):
        """Finds the true URL name of a package, when the given name isn't quite correct.
        This is usually used to implement case-insensitivity."""
        if not index_url.url.endswith('/'):
            # Vaguely part of the PyPI API... weird but true.
            ## FIXME: bad to modify this?
            index_url.url += '/'
        page = self._get_page(index_url, req)
        if page is None:
            logger.fatal('Cannot fetch index base URL %s' % index_url)
            raise DistributionNotFound('Cannot find requirement %s, nor fetch index URL %s' % (req, index_url))
        norm_name = normalize_name(req.url_name)
        for link in page.links:
            base = posixpath.basename(link.path.rstrip('/'))
            if norm_name == normalize_name(base):
                logger.notify('Real name of requirement %s is %s' % (url_name, base))
                return base
        raise DistributionNotFound('Cannot find requirement %s' % req)

    def _get_pages(self, locations, req):
        """Yields (page, page_url) from the given locations, skipping
        locations that have errors, and adding download/homepage links"""
        pending_queue = Queue()
        for location in locations:
            pending_queue.put(location)
        done = []
        seen = set()
        threads = []
        for i in range(min(10, len(locations))):
            t = threading.Thread(target=self._get_queued_page, args=(req, pending_queue, done, seen))
            t.setDaemon(True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return done

    _log_lock = threading.Lock()

    def _get_queued_page(self, req, pending_queue, done, seen):
        while 1:
            try:
                location = pending_queue.get(False)
            except QueueEmpty:
                return
            if location in seen:
                continue
            seen.add(location)
            page = self._get_page(location, req)
            if page is None:
                continue
            done.append(page)
            for link in page.rel_links():
                pending_queue.put(link)

    _egg_fragment_re = re.compile(r'#egg=([^&]*)')
    _egg_info_re = re.compile(r'([a-z0-9_.]+)-([a-z0-9_.-]+)', re.I)
    _py_version_re = re.compile(r'-py([123]\.[0-9])$')

    def _package_versions(self, links, search_name):
        seen_links = {}
        for link in links:
            if link.url in seen_links:
                continue
            seen_links[link.url] = None
            if link.egg_fragment:
                egg_info = link.egg_fragment
            else:
                path = link.path
                egg_info, ext = link.splitext()
                if not ext:
                    logger.debug('Skipping link %s; not a file' % link)
                    continue
                if egg_info.endswith('.tar'):
                    # Special double-extension case:
                    egg_info = egg_info[:-4]
                    ext = '.tar' + ext
                if ext not in ('.tar.gz', '.tar.bz2', '.tar', '.tgz', '.zip'):
                    logger.debug('Skipping link %s; unknown archive format: %s' % (link, ext))
                    continue
            version = self._egg_info_matches(egg_info, search_name, link)
            if version is None:
                logger.debug('Skipping link %s; wrong project name (not %s)' % (link, search_name))
                continue
            match = self._py_version_re.search(version)
            if match:
                version = version[:match.start()]
                py_version = match.group(1)
                if py_version != sys.version[:3]:
                    logger.debug('Skipping %s because Python version is incorrect' % link)
                    continue
            logger.debug('Found link %s, version: %s' % (link, version))
            yield (pkg_resources.parse_version(version),
                   link,
                   version)

    def _egg_info_matches(self, egg_info, search_name, link):
        match = self._egg_info_re.search(egg_info)
        if not match:
            logger.debug('Could not parse version from link: %s' % link)
            return None
        name = match.group(0).lower()
        # To match the "safe" name that pkg_resources creates:
        name = name.replace('_', '-')
        if name.startswith(search_name.lower()):
            return match.group(0)[len(search_name):].lstrip('-')
        else:
            return None

    def _get_page(self, link, req):
        return HTMLPage.get_page(link, req, cache=self.cache)


class InstallRequirement(object):

    def __init__(self, req, comes_from, source_dir=None, editable=False,
                 checkout_url=None):
        if isinstance(req, basestring):
            req = pkg_resources.Requirement.parse(req)
        self.req = req
        self.comes_from = comes_from
        self.source_dir = source_dir
        self.editable = editable
        if editable:
            assert checkout_url, "You must give checkout_url with editable=True"
        else:
            assert not checkout_url, "You cannot give checkout_url without editable=True"
        self.checkout_url = checkout_url
        self._egg_info_path = None
        # This holds the pkg_resources.Distribution object if this requirement
        # is already available:
        self.satisfied_by = None

    def __str__(self):
        s = str(self.req)
        if self.satisfied_by is not None:
            s += ' in %s' % display_path(self.satisfied_by.location)
        if self.editable:
            s += ' checkout from %s' % self.checkout_url
        if self.comes_from:
            if isinstance(self.comes_from, basestring):
                comes_from = self.comes_from
            else:
                comes_from = self.comes_from.from_path()
            s += ' (from %s)' % comes_from
        return s

    def from_path(self):
        s = str(self.req)
        if self.comes_from:
            if isinstance(self.comes_from, basestring):
                comes_from = self.comes_from
            else:
                comes_from = self.comes_from.from_path()
            s += '->' + comes_from
        return s

    def build_location(self, build_dir):
        if self.editable:
            name = self.name.lower()
        else:
            name = self.name
        return os.path.join(build_dir, name)

    @property
    def name(self):
        return self.req.project_name

    @property
    def url_name(self):
        return urllib.quote(self.req.unsafe_name)

    @property
    def setup_py(self):
        return os.path.join(self.source_dir, 'setup.py')

    def run_egg_info(self):
        assert self.source_dir
        logger.notify('Running setup.py egg_info for package %s' % self.name)
        logger.indent += 2
        try:
            script = self._run_setup_py
            script = script.replace('__SETUP_PY__', repr(self.setup_py))
            script = script.replace('__PKG_NAME__', repr(self.name))
            # We can't put the .egg-info files at the root, because then the source code will be mistaken
            # for an installed egg, causing problems
            if self.editable:
                egg_base_option = []
            else:
                egg_info_dir = os.path.join(self.source_dir, 'pyinstall-egg-info')
                if not os.path.exists(egg_info_dir):
                    os.makedirs(egg_info_dir)
                egg_base_option = ['--egg-base', 'pyinstall-egg-info']
            poacheggs.call_subprocess(
                [sys.executable, '-c', script, 'egg_info'] + egg_base_option,
                cwd=self.source_dir, filter_stdout=self._filter_install, show_stdout=False,
                command_level=poacheggs.Logger.VERBOSE_DEBUG,
                command_desc='python setup.py egg_info')
        finally:
            logger.indent -= 2

    ## FIXME: This is a lame hack, entirely for PasteScript which has
    ## a self-provided entry point that causes this awkwardness
    _run_setup_py = """
__file__ = __SETUP_PY__
from setuptools.command import egg_info
def replacement_run(self):
    self.mkpath(self.egg_info)
    installer = self.distribution.fetch_build_egg
    for ep in egg_info.iter_entry_points('egg_info.writers'):
        # require=False is the change we're making:
        writer = ep.load(require=False)
        writer(self, ep.name, egg_info.os.path.join(self.egg_info,ep.name))
    self.find_sources()
egg_info.egg_info.run = replacement_run
execfile(__file__)
"""

    def egg_info_data(self, filename):
        if self.satisfied_by is not None:
            if not self.satisfied_by.has_metadata(filename):
                return None
            return self.satisfied_by.get_metadata(filename)
        assert self.source_dir
        filename = self.egg_info_path(filename)
        if not os.path.exists(filename):
            return None
        fp = open(filename, 'r')
        data = fp.read()
        fp.close()
        return data

    def egg_info_path(self, filename):
        if self._egg_info_path is None:
            if self.editable:
                base = self.source_dir
            else:
                base = os.path.join(self.source_dir, 'pyinstall-egg-info')
            filenames = os.listdir(base)
            if self.editable:
                filenames = [f for f in filenames if f.endswith('.egg-info')]
            assert len(filenames) == 1, "Unexpected files/directories in %s: %s" % (base, ' '.join(filenames))
            self._egg_info_path = os.path.join(base, filenames[0])
        return os.path.join(self._egg_info_path, filename)

    def egg_info_lines(self, filename):
        data = self.egg_info_data(filename)
        if not data:
            return []
        result = []
        for line in data.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            result.append(line)
        return result

    def pkg_info(self):
        p = FeedParser()
        data = self.egg_info_data('PKG-INFO')
        if not data:
            logger.warn('No PKG-INFO file found in %s' % display_path(self.egg_info_path('PKG-INFO')))
        p.feed(data or '')
        return p.close()

    @property
    def dependency_links(self):
        return self.egg_info_lines('dependency_links.txt')

    _requirements_section_re = re.compile(r'\[(.*?)\]')

    def requirements(self, extras=()):
        in_extra = None
        for line in self.egg_info_lines('requires.txt'):
            match = self._requirements_section_re.match(line)
            if match:
                in_extra = match.group(1)
                continue
            if in_extra and in_extra not in extras:
                # Skip requirement for an extra we aren't requiring
                continue
            yield line

    @property
    def absolute_versions(self):
        for qualifier, version in self.req.specs:
            if qualifier == '==':
                yield version

    @property
    def installed_version(self):
        return self.pkg_info()['version']

    def assert_source_matches_version(self):
        assert self.source_dir
        if self.comes_from == 'command line':
            # We don't check the versions of things explicitly installed.
            # This makes, e.g., "pyinstall Package==dev" possible
            return
        version = self.installed_version
        if version not in self.req:
            logger.fatal(
                'Source in %s has the version %s, which does not match the requirement %s'
                % (display_path(self.source_dir), version, self))
            raise InstallationError(
                'Source in %s has version %s that conflicts with %s' 
                % (display_path(self.source_dir), version, self))
        else:
            logger.debug('Source in %s has version %s, which satisfies requirement %s'
                         % (display_path(self.source_dir), version, self))

    def update_editable(self):
        assert self.editable and self.checkout_url
        assert self.source_dir
        vc_type, url = self.checkout_url.split('+', 1)
        vc_type = vc_type.lower()
        if vc_type == 'svn':
            self.checkout_svn()
        else:
            assert 0, (
                'Unexpected version control type (in %s): %s' 
                % (self.checkout_url, vc_type))

    def checkout_svn(self):
        url = self.checkout_url.split('+', 1)[1]
        url = url.split('#', 1)[0]
        if '@' in url:
            url, rev = url.split('@', 1)
        else:
            rev = None
        if rev:
            rev_options = ['-r', rev]
            rev_display = ' (to revision %s)' % rev
        else:
            rev_options = []
            rev_display = ''
        dest = self.source_dir
        checkout = True
        if os.path.exists(os.path.join(self.source_dir, '.svn')):
            existing_url = self._get_svn_url(self.source_dir)
            checkout = False
            if existing_url == url:
                logger.info('Checkout in %s exists, and has correct URL (%s)'
                            % (display_path(self.source_dir), url))
                logger.notify('Updating checkout %s%s' % (display_path(self.source_dir), rev_display))
                poacheggs.call_subprocess(
                    ['svn', 'update'] + rev_options + [self.source_dir])
            else:
                logger.warn('svn checkout in %s exists with URL %s' % (display_path(self.source_dir), existing_url))
                logger.warn('The plan is to install the svn repository %s' % url)
                response = ask('What to do?  (s)witch, (i)gnore, (w)ipe, (b)ackup', ('s', 'i', 'w', 'b'))
                if response == 's':
                    logger.notify('Switching checkout %s to %s%s'
                                  % (display_path(self.source_dir), url, rev_display))
                    poacheggs.call_subprocess(
                        ['svn', 'switch'] + rev_options + [url, self.source_dir])
                elif response == 'i':
                    # do nothing
                    pass
                elif response == 'w':
                    logger.warn('Deleting %s' % display_path(self.source_dir))
                    shutil.rmtree(self.source_dir)
                    checkout = True
                elif response == 'b':
                    dest_dir = backup_dir(self.source_dir)
                    logger.warn('Backing up %s to %s' % display_path(self.source_dir, dest_dir))
                    shutil.move(self.source_dir, dest_dir)
                    checkout = True
        if checkout:
            logger.notify('Checking out %s%s to %s' % (url, rev_display, display_path(self.source_dir)))
            poacheggs.call_subprocess(
                ['svn', 'checkout', '-q'] + rev_options + [url, self.source_dir])

    _svn_url_re = re.compile(r'URL: (.+)')

    def _get_svn_url(self, dir):
        output = poacheggs.call_subprocess(['svn', 'info', dir], show_stdout=False,
                                           extra_environ={'LANG': 'C'})
        match = self._svn_url_re.search(output)
        if not match:
            logger.warn('Cannot determine URL of svn checkout %s' % display_path(dir))
            logger.info('Output that cannot be parsed: \n%s' % output)
            return 'unknown'
        return match.group(1).strip()

    def install(self):
        if self.editable:
            self.install_editable()
            return
        ## FIXME: this is not a useful record:
        ## Also a bad location
        record_filename = os.path.join(os.path.dirname(os.path.dirname(self.source_dir)), 'install-record-%s.txt' % self.name)
        ## FIXME: I'm not sure if this is a reasonable location; probably not
        ## but we can't put it in the default location, as that is a virtualenv symlink that isn't writable
        header_dir = os.path.join(os.path.dirname(os.path.dirname(self.source_dir)), 'lib', 'include')
        logger.notify('Running setup.py install for %s' % self.name)
        logger.indent += 2
        try:
            try:
                poacheggs.call_subprocess(
                    [sys.executable, '-c',
                     "import setuptools; __file__=%r; execfile(%r)" % (self.setup_py, self.setup_py), 
                     'install', '--single-version-externally-managed', '--record', record_filename,
                     '--install-headers', header_dir],
                    cwd=self.source_dir, filter_stdout=self._filter_install, show_stdout=False)
            except:
                raise
            else:
                if os.path.exists(self.delete_marker_filename):
                    logger.info('Removing source in %s' % self.source_dir)
                    shutil.rmtree(self.source_dir)
                    self.source_dir = None
        finally:
            logger.indent -= 2

    def install_editable(self):
        logger.notify('Running setup.py develop for %s' % self.name)
        logger.indent += 2
        try:
            ## FIXME: should we do --install-headers here too?
            poacheggs.call_subprocess(
                [sys.executable, '-c',
                 "import setuptools; __file__=%r; execfile(%r)" % (self.setup_py, self.setup_py),
                 'develop'], cwd=self.source_dir, filter_stdout=self._filter_install,
                show_stdout=False)
        finally:
            logger.indent -= 2

    def _filter_install(self, line):
        level = poacheggs.Logger.NOTIFY
        for regex in [r'^running .*', r'^writing .*', '^creating .*', '^[Cc]opying .*',
                      r'^reading .*', r"^removing .*\.egg-info' \(and everything under it\)$",
                      r'^byte-compiling ',
                      # Not sure what this warning is, but it seems harmless:
                      r"^warning: manifest_maker: standard file '-c' not found$"]:
            if re.search(regex, line.strip()):
                level = poacheggs.Logger.INFO
                break
        return (level, line)

    def check_if_exists(self):
        """Checks if this requirement is satisfied by something already installed"""
        try:
            dist = pkg_resources.get_distribution(self.req)
        except pkg_resources.DistributionNotFound:
            return False
        self.satisfied_by = dist
        return True

    @property
    def delete_marker_filename(self):
        assert self.source_dir
        return os.path.join(self.source_dir, 'pyinstall-delete-this-directory.txt')

DELETE_MARKER_MESSAGE = '''\
This file is placed here by pyinstall to indicate the source was put
here by pyinstall.

Once this package is successfully installed this source code will be
deleted (unless you remove this file).
'''

class RequirementSet(object):

    def __init__(self, build_dir, src_dir, upgrade=False, ignore_installed=False):
        self.build_dir = build_dir
        self.src_dir = src_dir
        self.upgrade = upgrade
        self.ignore_installed = ignore_installed
        self.requirements = {}

    def __str__(self):
        reqs = [req for req in self.requirements.values()
                if not req.comes_from]
        reqs.sort(key=lambda req: req.name.lower())
        return ' '.join([str(req.req) for req in reqs])

    def add_requirement(self, install_req):
        name = install_req.name
        if name in self.requirements:
            assert 0, (
                "Double required: %s (aready in %s, name=%r)"
                % (install_req, self.requirements[name], name))
        self.requirements[name] = install_req

    def install_files(self, finder):
        reqs = self.requirements.values()
        while reqs:
            req_to_install = reqs.pop(0)
            install = True
            if not self.ignore_installed and not req_to_install.editable:
                if req_to_install.check_if_exists():
                    if not self.upgrade:
                        # If we are upgrading, we still need to check the version
                        install = False
            if req_to_install.satisfied_by is not None:
                logger.notify('Requirement already satisfied: %s' % req_to_install)
            elif req_to_install.editable:
                logger.notify('Checking out %s' % req_to_install)
            else:
                logger.notify('Downloading/unpacking %s' % req_to_install)
            logger.indent += 2
            try:
                if req_to_install.editable:
                    location = req_to_install.build_location(self.src_dir)
                    req_to_install.source_dir = location
                    req_to_install.update_editable()
                    req_to_install.run_egg_info()
                elif install:
                    location = req_to_install.build_location(self.build_dir)
                    ## FIXME: is the existance of the checkout good enough to use it?  I'm don't think so.
                    unpack = True
                    if not os.path.exists(os.path.join(location, 'setup.py')):
                        ## FIXME: this won't upgrade when there's an existing package unpacked in `location`
                        url = finder.find_requirement(req_to_install, upgrade=self.upgrade)
                        if url:
                            try:
                                self.unpack_url(url, location)
                            except urllib2.HTTPError, e:
                                logger.fatal('Could not install requirement %s because of error %s'
                                             % (req_to_install, e))
                                raise InstallationError(
                                    'Could not install requirement %s because of HTTP error %s for URL %s'
                                    % (req_to_install, e, url))
                        else:
                            unpack = False
                    if unpack:
                        req_to_install.source_dir = location
                        req_to_install.run_egg_info()
                        req_to_install.assert_source_matches_version()
                        f = open(req_to_install.delete_marker_filename, 'w')
                        f.write(DELETE_MARKER_MESSAGE)
                        f.close()
                ## FIXME: shouldn't be globally added:
                finder.add_dependency_links(req_to_install.dependency_links)
                ## FIXME: add extras in here:
                for req in req_to_install.requirements():
                    try:
                        name = pkg_resources.Requirement.parse(req).project_name
                    except ValueError, e:
                        ## FIXME: proper warning
                        logger.error('Invalid requirement: %r (%s) in requirement %s' % (req, e, req_to_install))
                        continue
                    if name in self.requirements:
                        ## FIXME: check for conflict
                        continue
                    subreq = InstallRequirement(req, req_to_install)
                    reqs.append(subreq)
                    self.add_requirement(subreq)
                if req_to_install.name not in self.requirements:
                    self.requirements[name] = req_to_install
            finally:
                logger.indent -= 2

    def unpack_url(self, link, location):
        if link.scheme == 'svn' or link.scheme == 'svn+ssh':
            self.svn_export(link, location)
            return
        dir = tempfile.mkdtemp()
        md5_hash = link.md5_hash
        try:
            resp = urllib2.urlopen(link.url.split('#', 1)[0])
        except urllib2.HTTPError, e:
            logger.fatal("HTTP error %s while getting %s" % (e.code, link))
            raise
        except IOError, e:
            # Typically an FTP error
            logger.fatal("Error %s while getting %s" % (e, link))
            raise
        content_type = resp.info()['content-type']
        filename = link.filename
        ext = os.path.splitext(filename)
        if not ext:
            ext = mimetypes.guess_extension(content_type)
            filename += ext
        temp_location = os.path.join(dir, filename)
        fp = open(temp_location, 'wb')
        if md5_hash:
            download_hash = md5.new()
        try:
            total_length = int(resp.info()['content-length'])
        except (ValueError, KeyError):
            total_length = 0
        downloaded = 0
        show_progress = total_length > 40*1000 or not total_length
        show_url = link.show_url
        try:
            if show_progress:
                ## FIXME: the URL can get really long in this message:
                if total_length:
                    logger.start_progress('Downloading %s (%s): ' % (show_url, format_size(total_length)))
                else:
                    logger.start_progress('Downloading %s (unknown size): ' % show_url)
            else:
                logger.notify('Downloading %s' % show_url)
            while 1:
                chunk = resp.read(4096)
                if not chunk:
                    break
                downloaded += len(chunk)
                if show_progress:
                    if not total_length:
                        logger.show_progress('%s' % format_size(downloaded))
                    else:
                        logger.show_progress('%3i%%  %s' % (100*downloaded/total_length, format_size(downloaded)))
                if md5_hash:
                    download_hash.update(chunk)
                fp.write(chunk)
            fp.close()
        finally:
            if show_progress:
                logger.end_progress('%s downloaded' % format_size(downloaded))
        if md5_hash:
            download_hash = download_hash.hexdigest()
            if download_hash != md5_hash:
                logger.fatal("MD5 hash of the package %s (%s) doesn't match the expected hash %s!"
                             % (link, download_hash, md5_hash))
                raise InstallationError('Bad MD5 hash for package %s' % link)
        self.unpack_file(temp_location, location, content_type, link)
        os.unlink(temp_location)

    def unpack_file(self, filename, location, content_type, link):
        if (content_type == 'application/zip'
            or filename.endswith('.zip')):
            self.unzip_file(filename, location)
        elif (content_type == 'application/x-gzip'
              or tarfile.is_tarfile(filename)
              ## FIXME: not sure if splitext will ever produce .tar.gz:
              or os.path.splitext(filename)[1].lower() in ('.tar', '.tar.gz', '.tar.bz2', '.tgz')):
            self.untar_file(filename, location)
        elif (content_type.startswith('text/html')
              and is_svn_page(file_contents(filename))):
            # We don't really care about this
            self.svn_export(link.url, location)
        else:
            ## FIXME: handle?
            ## FIXME: magic signatures?
            logger.fatal('Cannot unpack file %s (downloaded from %s, content-type: %s); cannot detect archive format'
                         % (filename, url, content_type))
            raise InstallationError('Cannot determine archive format of %s' % url)

    def unzip_file(self, filename, location):
        """Unzip the file (zip file located at filename) to the destination
        location"""
        if not os.path.exists(location):
            os.makedirs(location)
        zipfp = open(filename, 'rb')
        try:
            zip = zipfile.ZipFile(zipfp)
            leading = has_leading_dir(zip.namelist())
            for name in zip.namelist():
                data = zip.read(name)
                fn = name
                if leading:
                    fn = split_leading_dir(name)[1]
                fn = os.path.join(location, fn)
                dir = os.path.dirname(fn)
                if not os.path.exists(dir):
                    os.makedirs(dir)
                if fn.endswith('/'):
                    # A directory
                    if not os.path.exists(fn):
                        os.makedirs(fn)
                else:
                    fp = open(fn, 'wb')
                    try:
                        fp.write(data)
                    finally:
                        fp.close()
        finally:
            zipfp.close()

    def untar_file(self, filename, location):
        """Untar the file (tar file located at filename) to the destination location"""
        if not os.path.exists(location):
            os.makedirs(location)
        if filename.lower().endswith('.gz') or filename.lower().endswith('.tgz'):
            mode = 'r:gz'
        elif filename.lower().endswith('.bz2'):
            mode = 'r:bz2'
        elif filename.lower().endswith('.tar'):
            mode = 'r'
        else:
            logger.warn('Cannot determine compression type for file %s' % filename)
            mode = 'r:*'
        tar = tarfile.open(filename, mode)
        try:
            leading = has_leading_dir([member.name for member in tar.getmembers()])
            for member in tar.getmembers():
                fn = member.name
                if leading:
                    fn = split_leading_dir(fn)[1]
                path = os.path.join(location, fn)
                if member.isdir():
                    if not os.path.exists(path):
                        os.makedirs(path)
                else:
                    fp = tar.extractfile(member)
                    if not os.path.exists(os.path.dirname(path)):
                        os.makedirs(os.path.dirname(path))
                    destfp = open(path, 'wb')
                    try:
                        shutil.copyfileobj(fp, destfp)
                    finally:
                        destfp.close()
                    fp.close()
        finally:
            tar.close()

    def svn_export(self, url, location):
        """Export the svn repository at the url to the destination location"""
        if '#' in url:
            url = url.split('#', 1)[0]
        logger.notify('Exporting svn repository %s to %s' % (url, location))
        logger.indent += 2
        try:
            poacheggs.call_subprocess(['svn', 'export', url, location],
                                      filter_stdout=self._filter_svn, show_stdout=False)
        finally:
            logger.indent -= 2

    def _filter_svn(self, line):
        return (poacheggs.Logger.INFO, line)

    def install(self):
        """Install everything in this set (after having downloaded and unpacked the packages)"""
        requirements = sorted(self.requirements.values(), key=lambda p: p.name.lower())
        logger.notify('Installing collected packages: %s' % (', '.join([req.name for req in requirements])))
        logger.indent += 2
        try:
            for requirement in self.requirements.values():
                if requirement.satisfied_by is not None:
                    # Already installed
                    continue
                requirement.install()
        finally:
            logger.indent -= 2

class HTMLPage(object):
    """Represents one page, along with its URL"""

    ## FIXME: these regexes are horrible hacks:
    _homepage_re = re.compile(r'<th>\s*home\s*page', re.I)
    _download_re = re.compile(r'<th>\s*download\s+url', re.I)
    ## These aren't so aweful:
    _rel_re = re.compile("""<[^>]*\srel\s*=\s*['"]?([^'">]+)[^>]*>""", re.I)
    _href_re = re.compile('href=(?:"([^"]*)"|\'([^\']*)\'|([^>\\s\\n]*))', re.I|re.S)

    def __init__(self, content, url, headers=None):
        self.content = content
        self.url = url
        self.headers = headers

    def __str__(self):
        return self.url

    @classmethod
    def get_page(cls, link, req, cache=None, skip_archives=True):
        url = link.url
        url = url.split('#', 1)[0]
        if cache.too_many_failures(url):
            return None
        if url.lower().startswith('svn'):
            logger.debug('Cannot look at svn URL %s' % link)
            return None
        if cache is not None:
            inst = cache.get_page(url)
            if inst is not None:
                return inst
        if skip_archives:
            if cache is not None:
                if cache.is_archive(url):
                    return None
            filename = link.filename
            for bad_ext in ['.tar', '.tar.gz', '.tar.bz2', '.tgz', '.zip']:
                if filename.endswith(bad_ext):
                    content_type = cls._get_content_type(url)
                    if content_type.lower().startswith('text/html'):
                        break
                    else:
                        logger.debug('Skipping page %s because of Content-Type: %s' % (link, content_type))
                        if cache is not None:
                            cache.set_is_archive(url)
                        return None
        try:
            logger.debug('Getting page %s' % url)
            resp = urllib2.urlopen(url)
            real_url = resp.geturl()
            headers = resp.info()
            inst = cls(resp.read(), real_url, headers)
        except urllib2.HTTPError, e:
            if e.code == 404:
                ## FIXME: notify?
                log_meth = logger.info
                level = 2
            else:
                log_meth = logger.warn
                level = 1
            log_meth('Could not fetch URL %s: %s (for requirement %s)' % (link, e, req))
            if cache is not None:
                cache.add_page_failure(url, level)
            return None
        except urllib2.URLError, e:
            logger.warn('URL %s is invalid: %s' % (link, e))
            if cache is not None:
                cache.add_page_failure(url, 2)
            return None
        if cache is not None:
            cache.add_page([url, real_url], inst)
        return inst

    @staticmethod
    def _get_content_type(url):
        """Get the Content-Type of the given url, using a HEAD request"""
        scheme, netloc, path, query, fragment = urlparse.urlsplit(url)
        if scheme == 'http':
            ConnClass = httplib.HTTPConnection
        elif scheme == 'https':
            ConnClass = httplib.HTTPSConnection
        else:
            ## FIXME: some warning or something?
            ## assertion error?
            return ''
        if query:
            path += '?' + query
        conn = ConnClass(netloc)
        try:
            conn.request('HEAD', path, headers={'Host': netloc})
            resp = conn.getresponse()
            if resp.status != 200:
                ## FIXME: doesn't handle redirects
                return ''
            return resp.getheader('Content-Type') or ''
        finally:
            conn.close()

    @property
    def links(self):
        """Yields all links in the page"""
        for match in self._href_re.finditer(self.content):
            url = match.group(1) or match.group(2) or match.group(3)
            yield Link(urlparse.urljoin(self.url, url), self)

    def rel_links(self):
        for url in self.explicit_rel_links():
            yield url
        for url in self.scraped_rel_links():
            yield url

    def explicit_rel_links(self, rels=('homepage', 'download')):
        """Yields all links with the given relations"""
        for match in self._rel_re.finditer(self.content):
            found_rels = match.group(1).lower().split()
            for rel in rels:
                if rel in found_rels:
                    break
            else:
                continue
            match = self._href_re.search(match.group(0))
            if not match:
                continue
            url = match.group(1) or match.group(2) or match.group(3)
            yield Link(urlparse.urljoin(self.url, url), self)

    def scraped_rel_links(self):
        for regex in (self._homepage_re, self._download_re):
            match = regex.search(self.content)
            if not match:
                continue
            href_match = self._href_re.search(self.content, pos=match.end())
            if not href_match:
                continue
            url = match.group(1) or match.group(2) or match.group(3)
            if not url:
                continue
            url = urlparse.urljoin(self.url, url)
            yield Link(url, self)

class PageCache(object):
    """Cache of HTML pages"""

    failure_limit = 3

    def __init__(self):
        self._failures = {}
        self._pages = {}
        self._archives = {}

    def too_many_failures(self, url):
        return self._failures.get(url, 0) >= self.failure_limit

    def get_page(self, url):
        return self._pages.get(url)

    def is_archive(self, url):
        return self._archives.get(url, False)

    def set_is_archive(self, url, value=True):
        self._archives[url] = value

    def add_page_failure(self, url, level):
        self._failures[url] = self._failures.get(url, 0)+level

    def add_page(self, urls, page):
        for url in urls:
            self._pages[url] = page

class Link(object):

    def __init__(self, url, comes_from=None):
        self.url = url
        self.comes_from = comes_from
    
    def __str__(self):
        if self.comes_from:
            return '%s (from %s)' % (self.url, self.comes_from)
        else:
            return self.url

    def __repr__(self):
        return '<Link %s>' % self

    @property
    def filename(self):
        url = self.url
        url = url.split('#', 1)[0]
        url = url.split('?', 1)[0]
        url = url.rstrip('/')
        name = posixpath.basename(url)
        assert name, (
            'URL %r produced no filename' % url)
        return name

    @property
    def scheme(self):
        return urlparse.urlsplit(self.url)[0]

    @property
    def path(self):
        return urlparse.urlsplit(self.url)[2]

    def splitext(self):
        base, ext = posixpath.splitext(posixpath.basename(self.path.rstrip('/')))
        if base.endswith('.tar'):
            ext = '.tar' + ext
            base = base[:-4]
        return base, ext

    _egg_fragment_re = re.compile(r'#egg=([^&]*)')

    @property
    def egg_fragment(self):
        match = self._egg_fragment_re.search(self.url)
        if not match:
            return None
        return match.group(1)

    _md5_re = re.compile(r'md5=([a-f0-9]+)')

    @property
    def md5_hash(self):
        match = self._md5_re.search(self.url)
        if match:
            return match.group(1)
        return None

    @property
    def show_url(self):
        return posixpath.basename(self.url.split('#', 1)[0].split('?', 1)[0])

############################################################
## Utility functions

def is_svn_page(html):
    """Returns true if the page appears to be the index page of an svn repository"""
    return (re.search(r'<title>[^<]*Revision \d+:', html)
            and re.search(r'Powered by (?:<a[^>]*?>)?Subversion', html, re.I))

def file_contents(filename):
    fp = open(filename, 'rb')
    try:
        return fp.read()
    finally:
        fp.close()

_no_default = ()
def split_leading_dir(path, default=_no_default):
    path = str(path)
    path = path.lstrip('/').lstrip('\\')
    if '/' in path and (('\\' in path and path.find('/') < path.find('\\'))
                        or '\\' not in path):
        return path.split('/', 1)
    elif '\\' in path:
        return path.split('\\', 1)
    elif default is not _no_default:
        return default, path
    else:
        assert 0, 'No directories in path: %r' % path

def has_leading_dir(paths):
    """Returns true if all the paths have the same leading path name
    (i.e., everything is in one subdirectory in an archive)"""
    common_prefix = None
    for path in paths:
        prefix, rest = split_leading_dir(path, default=None)
        if prefix is None:
            return False
        elif common_prefix is None:
            common_prefix = prefix
        elif prefix != common_prefix:
            return False
    return True

def format_size(bytes):
    if bytes > 1000*1000:
        return '%.1fMb' % (bytes/1000.0/1000)
    elif bytes > 10*1000:
        return '%iKb' % (bytes/1000)
    elif bytes > 1000:
        return '%.1fKb' % (bytes/1000.0)
    else:
        return '%ibytes' % bytes

_normalize_re = re.compile(r'[^a-z]', re.I)

def normalize_name(name):
    return _normalize_re.sub('-', name.lower())

def display_path(path):
    """Gives the display value for a given path, making it relative to cwd
    if possible."""
    path = os.path.normcase(os.path.abspath(path))
    if path.startswith(os.getcwd() + os.path.sep):
        path = '.' + path[len(os.getcwd()):]
    return path

def parse_editable(editable_req):
    """Parses svn+http://blahblah@rev#egg=Foobar into a requirement
    (Foobar) and a URL"""
    match = re.search(r'(?:#|#.*?&)egg=([^&]*)', editable_req)
    if not match or not match.group(1):
        raise InstallationError(
            '--editable=%s is not the right format; it must have #egg=Package'
            % editable_req)
    req = match.group(1)
    match = re.search(r'^(.*?)(?:-dev|-\d.*)', req)
    if match:
        # Strip off -dev, -0.2, etc.
        req = match.group(1)
    url = editable_req
    if url.lower().startswith('svn:'):
        url = 'svn+' + url
    if '+' not in url:
        raise InstallationError(
            '--editable=%s should be formatted with svn+URL' % editable_req)
    vc_type = url.split('+', 1)[0].lower()
    if vc_type != 'svn':
        raise InstallationError(
            'For --editable=%s only svn (svn+URL) is currently supported' % editable_req)
    return req, url

def backup_dir(dir):
    """Figure out the name of a directory to back up the given dir to
    (adding .bak, .bak2, etc)"""
    n = 1
    ext = '.bak'
    while os.path.exists(dir + ext):
        n += 1
        ext = '.bak%s' % n
    return dir + ext

def ask(message, options):
    """Ask the message interactively, with the given possible responses"""
    while 1:
        response = raw_input(message)
        response = response.strip().lower()
        if response not in options:
            print 'Your response (%r) was not one of the expected responses: %s' % (
                response, ', '.join(options))
        else:
            return response

class _Inf(object):
    """I am bigger than everything!"""
    def __cmp__(self, a):
        if self is a:
            return 0
        return 1
    def __repr__(self):
        return 'Inf'
Inf = _Inf()
del _Inf

if __name__ == '__main__':
    main()

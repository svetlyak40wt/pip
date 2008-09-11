#!/usr/bin/env python
import sys
import os
import optparse
import pkg_resources
import urllib2
import mimetypes
import zipfile
import tarfile
import tempfile
import subprocess
import posixpath
import re
import shutil
import md5
import poacheggs

class InstallationError(Exception):
    """General exception during installation"""

class DistributionNotFound(InstallationError):
    """Raised when a distribution cannot be found to satisfy a requirement"""

if getattr(sys, 'real_prefix', None):
    ## FIXME: is src/ really good?  Should it be something to imply these are just installation files?
    base_prefix = os.path.join(sys.prefix, 'src')
else:
    ## FIXME: this isn't a very good default
    base_prefix = os.path.join(os.getcwd(), 'build')

pypi_url = "http://pypi.python.org/simple"

parser = optparse.OptionParser(
    usage='%prog [OPTIONS] PACKAGE_NAMES')

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
    level = poacheggs.Logger.level_for_integer(3-level)
    logger = poacheggs.Logger([(level, sys.stdout)])
    ## FIXME: this is hacky :(
    poacheggs.logger = logger

    index_urls = [options.index_url] + options.extra_index_urls
    finder = PackageFinder(
        find_links=options.find_links,
        index_urls=index_urls)
    requirement_set = RequirementSet(build_dir=options.build_dir)
    for name in args:
        requirement_set.add_requirement(InstallRequirement(name, '<command-line>'))
    requirement_set.install_packages(finder)
    requirement_set.install()

class PackageFinder(object):
    """This finds packages.

    This is meant to match easy_install's technique for looking for
    packages, by reading pages and looking for appropriate links
    """

    def __init__(self, find_links, index_urls):
        self.find_links = find_links
        self.index_urls = index_urls
        self.dependency_links = []
        self.cached_pages = {}
    
    def add_dependency_links(self, links):
        self.dependency_links.extend(links)

    def find_requirement(self, req):
        locations = [
            posixpath.join(url, req.name)
            for url in self.index_urls] + self.find_links + self.dependency_links
        for version in req.absolute_versions:
            locations = [
                posixpath.join(url, req.name, version)] + locations
        found_versions = []
        for location in locations:
            try:
                page = self._get_page(location)
            except urllib2.HTTPError, e:
                if e.code == 404:
                    log_meth = logger.info
                else:
                    log_meth = logger.warn
                log_meth('Could not fetch URL %s: %s (for requirement %s)' % (location, e, req))
                continue
            found_versions.extend(self._package_versions(self._parse_links(page), req.name.lower()))
        if not found_versions:
            logger.fatal('Could not find any downloads that satisfy the requirement %s' % req)
            raise DistributionNotFound('No distributions at all found for %s' % req)
        found_versions.sort(reverse=True)
        applicable_versions = []
        for (parsed_version, link, version) in found_versions:
            if version not in req.req:
                logger.info("Removing link %s, version %s doesn't match %s"
                            % (link, version, ','.join([''.join(s) for s in req.req.specs])))
                continue
            applicable_versions.append((link, version))
        if len(applicable_versions) > 1:
            logger.info('Using version %s (newest of versions: %s)' %
                        (applicable_versions[0][1], ', '.join([version for link, version in applicable_versions])))
        elif not applicable_versions:
            print found_versions
            logger.fatal('Could not find a version that satisfies the requirement %s (from versions: %s)'
                         % (req, ', '.join([version for parsed_version, link, version in found_versions])))
            raise DistributionNotFound('No distributions matching the version for %s' % req)
        return applicable_versions[0][0]

    _egg_fragment_re = re.compile(r'#egg=([^&]*)')
    _egg_info_re = re.compile(r'([a-z0-9_.]+)-([a-z0-9_.-]+)', re.I)
    _py_version_re = re.compile(r'-py([123]\.[0-9])$')

    def _package_versions(self, links, search_name):
        for link in links:
            if '#egg' in link:
                egg_info = self._egg_fragment_re.search(link).group(1)
            else:
                egg_info = posixpath.splitext(posixpath.basename(link.rstrip('/')))[0]
                if egg_info.endswith('.tar'):
                    # Special double-extension case:
                    egg_info = egg_info[:-4]
            match = self._egg_info_re.search(egg_info)
            if not match:
                logger.debug('Could not parse version from link: %s' % link)
                continue
            name = match.group(1).lower()
            if name != search_name:
                continue
            version = match.group(2)
            match = self._py_version_re.search(version)
            if match:
                version = version[:match.start()]
                py_version = match.group(1)
                if py_version != sys.version[:3]:
                    logger.debug('Skipping %s because Python version is incorrect' % link)
                    continue
            yield (pkg_resources.parse_version(version),
                   link,
                   version)

    def _get_page(self, url):
        if url not in self.cached_pages:
            resp = urllib2.urlopen(url)
            page = resp.read()
            self.cached_pages[url] = page
        else:
            page = self.cached_pages[url]
        return page

    _href_re = re.compile('href=(?:"([^"]*)"|\'([^\']*)\'|([^>\\s\\n]*))', re.I|re.S)

    def _parse_links(self, page):
        for match in self._href_re.finditer(page):
            yield match.group(1) or match.group(2) or match.group(3)
            

class InstallRequirement(object):

    def __init__(self, req, comes_from, built_dir=None, installed=False):
        if isinstance(req, basestring):
            req = pkg_resources.Requirement.parse(req)
        self.req = req
        self.comes_from = comes_from
        self.built_dir = built_dir
        self.installed = installed

    def __str__(self):
        s = str(self.req)
        if self.comes_from:
            s += ' (from %s)' % self.comes_from
        if self.installed:
            s += ' installed'
        return s

    def build_location(self, build_dir):
        return os.path.join(build_dir, self.name)

    @property
    def name(self):
        return self.req.project_name

    @property
    def setup_py(self):
        return os.path.join(self.built_dir, 'setup.py')

    def run_egg_info(self):
        assert self.built_dir
        logger.notify('Running setup.py egg_info for package %s' % self.name)
        logger.indent += 2
        try:
            poacheggs.call_subprocess([sys.executable, self.setup_py, 'egg_info'], cwd=self.built_dir,
                                      filter_stdout=self._filter_install, show_stdout=False)
        finally:
            logger.indent -= 2

    def egg_info_data(self, filename):
        assert self.built_dir
        fn = os.path.join(self.built_dir, self.name + '.egg-info', filename)
        if not os.path.exists(fn):
            return None
        fp = open(fn, 'r')
        data = fp.read()
        fp.close()
        return data

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

    def install(self):
        ## FIXME: this is not a useful record:
        ## Also a bad location
        record_filename = os.path.join(os.path.dirname(os.path.dirname(self.built_dir)), 'install-record.txt')
        logger.notify('Running setup.py install for %s' % self.name)
        logger.indent += 2
        try:
            poacheggs.call_subprocess(
                [sys.executable, '-c',
                 "import setuptools; __file__=%r; execfile(%r)" % (self.setup_py, self.setup_py), 
                 'install', '--single-version-externally-managed', '--record', record_filename],
                cwd=self.built_dir, filter_stdout=self._filter_install, show_stdout=False)
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

class RequirementSet(object):

    def __init__(self, build_dir):
        self.build_dir = build_dir
        self.requirements = {}

    def add_requirement(self, install_req):
        name = install_req.name
        if name in self.requirements:
            assert 0, (
                "Double required: %s (aready in %s, name=%r)"
                % (install_req, self.requirements[name], name))
        self.requirements[name] = install_req

    def install_packages(self, finder):
        reqs = self.requirements.values()
        while reqs:
            req_to_install = reqs.pop(0)
            logger.notify('Downloading/unpacking %s' % req_to_install.name)
            logger.indent += 2
            try:
                location = req_to_install.build_location(self.build_dir)
                if not os.path.exists(os.path.join(location, 'setup.py')):
                    url = finder.find_requirement(req_to_install)
                    self.unpack_url(url, location)
                req_to_install.built_dir = location
                req_to_install.run_egg_info()
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
                    reqs.append(InstallRequirement(req, req_to_install))
                if req_to_install.name not in self.requirements:
                    self.requirements[name] = req_to_install
            finally:
                logger.indent -= 2

    _md5_re = re.compile(r'md5=([a-f0-9]+)')

    def unpack_url(self, url, location):
        dir = tempfile.mkdtemp()
        match = self._md5_re.search(url)
        if match:
            md5hash = match.group(1)
        else:
            md5hash = None
        print 'hash on url %s: %s' % (url, md5hash)
        resp = urllib2.urlopen(url.split('#', 1)[0])
        content_type = resp.info()['content-type']
        filename = filename_for_url(url)
        ext = os.path.splitext(filename)
        if not ext:
            ext = mimetypes.guess_extension(content_type)
            filename += ext
        temp_location = os.path.join(dir, filename)
        fp = open(temp_location, 'wb')
        if md5hash:
            download_hash = md5.new()
        while 1:
            chunk = resp.read(4096)
            if not chunk:
                break
            if md5hash:
                download_hash.update(chunk)
            fp.write(chunk)
        fp.close()
        if md5hash:
            download_hash = download_hash.hexdigest()
            if download_hash != md5hash:
                logger.fatal("MD5 hash of the package %s (%s) doesn't match the expected hash %s!"
                             % (url, download_hash, md5hash))
                raise InstallationError('Bad MD5 hash for package %s' % url)
        self.unpack_file(temp_location, location, content_type, url)
        os.unlink(temp_location)

    def unpack_file(self, filename, location, content_type, url):
        if content_type == 'application/zip':
            self.unzip_file(filename, location)
        elif (content_type == 'application/x-gzip'
              or tarfile.is_tarfile(filename)):
            ## FIXME: bz2, etc?
            self.untar_file(filename, location)
        elif (content_type.startswith('text/html')
              and is_svn_page(file_contents(filename))):
            # We don't really care about this
            self.svn_export(url, location)
        else:
            ## FIXME: handle?
            ## FIXME: magic signatures?
            logger.fatal('Cannot unpack file %s (downloaded from %s); cannot detect archive format'
                         % (filename, url))
            raise InstallationError('Cannot determine archive format of %s' % url)

    def unzip_file(self, filename, location):
        """Unzip the file (zip file located at filename) to the destination
        location"""
        if not os.path.exists(location):
            os.makedirs(location)
        zipfp = open(location, 'rb')
        try:
            zip = zipfile(zipfp)
            for name in zip.namelist():
                data = zip.read(name)
                fn = os.path.join(location, strip_leading_dir(name))
                dir = os.path.dirname(fn)
                if not os.path.exists(dir):
                    os.makedirs(dir)
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
        if filename.lower().endswith('.gz'):
            mode = 'r:gz'
        elif filename.lower().endswith('.bz2'):
            mode = 'r:bz2'
        elif filename.lower().endswith('.tar'):
            mode = 'r'
        else:
            logger.debug('Cannot determine compression type for file %s' % filename)
            mode = 'r:*'
        tar = tarfile.open(filename, mode)
        try:
            for member in tar.getmembers():
                path = os.path.join(location, strip_leading_dir(member.name))
                if member.isdir():
                    if not os.path.exists(path):
                        os.makedirs(path)
                else:
                    fp = tar.extractfile(member)
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
        for requirement in self.requirements.values():
            requirement.install()

############################################################
## Utility functions

def is_svn_page(html):
    """Returns true if the page appears to be the index page of an svn repository"""
    return (re.search(r'<title>Revision \d+:', html)
            and re.search(r'Powered by (?:<a[^>]*?>)?Subversion', html))

def file_contents(filename):
    fp = open(filename, 'rb')
    try:
        return fp.read()
    finally:
        fp.close()

def filename_for_url(url):
    url = url.rstrip('/')
    url = url.split('#', 1)[0]
    name = posixpath.basename(url)
    return name

def strip_leading_dir(path):
    path = str(path)
    path = path.lstrip('/').lstrip('\\')
    if '/' in path and (('\\' in path and path.find('/') < path.find('\\'))
                        or '\\' not in path):
        return path.split('/', 1)[1]
    elif '\\' in path:
        return path.split('\\', 1)[1]
    else:
        assert 0, 'No directories in path: %r' % path

if __name__ == '__main__':
    main()

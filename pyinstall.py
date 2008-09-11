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

if getattr(sys, 'real_prefix', None):
    base_prefix = os.path.join(sys.prefix, 'src')
else:
    ## FIXME: this isn't a very good default
    base_prefix = os.getcwd()

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

def main(args=None):
    if args is None:
        args = sys.argv[1:]
    options, args = parser.parse_args(args)
    index_urls = [options.index_url] + options.extra_index_urls
    finder = PackageFinder(
        find_links=options.find_links,
        index_urls=index_urls)
    requirement_set = RequirementSet(build_dir=options.build_dir)
    for name in args:
        requirement_set.add_requirement(InstallRequirement(name, '<command-line>'))
    requirement_set.install_packages(finder)
    requirement_set.install()

def filename_for_url(url):
    url = url.rstrip('/')
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

class PackageFinder(object):

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
                ## FIXME: better warning:
                print 'Could not fetch URL %s: %s' % (location, e)
                continue
            found_versions.extend(self._package_versions(self._parse_links(page), req.name.lower()))
        found_versions.sort(reverse=True)
        ## FIXME: proper error
        assert found_versions, (
            "Nothing found matching requirement %s" % req)
        return found_versions[0][1]

    _egg_fragment_re = re.compile(r'#egg=([^&]*)')
    _egg_info_re = re.compile(r'([a-z0-9_.]+)-([a-z0-9_.-])', re.I)

    def _package_versions(self, links, search_name):
        for link in links:
            if '#egg' in link:
                egg_info = self._egg_fragment_re.search(link).group(1)
            else:
                egg_info = posixpath.basename(link.rstrip('/'))
            match = self._egg_info_re.search(egg_info)
            if not match:
                continue
            name = match.group(1).lower()
            if name != search_name:
                continue
            yield (pkg_resources.parse_version(match.group(2)),
                   link)

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
        subprocess.call([sys.executable, self.setup_py, 'egg_info'], cwd=self.built_dir)

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
        subprocess.call([sys.executable, '-c',
                         "import setuptools; __file__=%r; execfile(%r)" % (self.setup_py, self.setup_py), 
                         'install', '--single-version-externally-managed', '--record', record_filename],
                        cwd=self.built_dir)

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
                    print 'Invalid requirement: %r (%s)' % (req, e)
                    continue
                if name in self.requirements:
                    ## FIXME: check for conflict
                    continue
                reqs.append(InstallRequirement(req, req_to_install))
            if req_to_install.name not in self.requirements:
                self.requirements[name] = req_to_install

    def unpack_url(self, url, location):
        if '#' in url:
            url = url.split('#')[0]
        dir = tempfile.mkdtemp()
        resp = urllib2.urlopen(url)
        content_type = resp.info()['content-type']
        filename = filename_for_url(url)
        ext = os.path.splitext(filename)
        if not ext:
            ext = mimetypes.guess_extension(content_type)
            filename += ext
        temp_location = os.path.join(dir, filename)
        fp = open(temp_location, 'wb')
        while 1:
            chunk = resp.read(4096)
            if not chunk:
                break
            fp.write(chunk)
        fp.close()
        self.unpack_file(temp_location, location, content_type)
        os.unlink(temp_location)

    def unpack_file(self, filename, location, content_type):
        if content_type == 'application/zip':
            self.unzip_file(filename, location)
        elif (content_type == 'application/x-gzip'
              or tarfile.is_tarfile(filename)):
            ## FIXME: bz2, etc?
            self.untar_file(filename, location)
        else:
            ## FIXME: handle?
            ## FIXME: magic signatures?
            assert 0

    def unzip_file(self, filename, location):
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
        if not os.path.exists(location):
            os.makedirs(location)
        if filename.lower().endswith('.gz'):
            mode = 'r:gz'
        elif filename.lower().endswith('.bz2'):
            mode = 'r:bz2'
        elif filename.lower().endswith('.tar'):
            mode = 'r'
        else:
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

    def install(self):
        for requirement in self.requirements.values():
            requirement.install()

if __name__ == '__main__':
    main()

import doctest
import os, sys

pyversion = sys.version[:3]
lib_py = 'lib/python%s/' % pyversion
here = os.path.dirname(os.path.abspath(__file__))
base_path = os.path.join(here, 'test-scratch')

from scripttest import TestFileEnvironment

def reset_env():
    global env
    env = TestFileEnvironment(base_path, ignore_hidden=False)
    env.run('virtualenv', env.base_path)
    env.run('mkdir', 'src')

def run_poach(*args, **kw):
    import sys
    args = ('python', '../../poacheggs.py', '-E', env.base_path) + args
    #print >> sys.__stdout__, 'running', ' '.join(args)
    if options.show_error:
        kw['expect_error'] = True
    result = env.run(*args, **kw)
    if options.show_error and result.returncode:
        print result
    return result

def write_file(filename, text):
    f = open(os.path.join(base_path, filename), 'w')
    f.write(text)
    f.close()

import optparse
parser = optparse.OptionParser(usage='%prog [OPTIONS] [TEST_FILE...]')
parser.add_option('--first', action='store_true',
                  help='Show only first failure')
parser.add_option('--diff', action='store_true',
                  help='Show diffs in doctest failures')
parser.add_option('--show-error', action='store_true',
                  help='Show the errors (use expect_error=True in run_poach)')

def main():
    global options
    options, args = parser.parse_args()
    if not args:
        args = ['test_basic.txt', 'test_requirements.txt', 'test_collect.txt']
    optionflags = doctest.ELLIPSIS
    if options.first:
        optionflags |= doctest.REPORT_ONLY_FIRST_FAILURE
    if options.diff:
        optionflags |= doctest.REPORT_UDIFF
    for filename in args:
        if not filename.endswith('.txt'):
            filename += '.txt'
        if not filename.startswith('test_'):
            filename = 'test_' + filename
        ## FIXME: test for filename existance
        doctest.testfile(filename, optionflags=optionflags)

if __name__ == '__main__':
    main()

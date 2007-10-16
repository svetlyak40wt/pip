#!/bin/sh

if [ -n "$WORKING_ENV" ] ; then
    echo "Deactivate the workingenv before running this script."
    exit 1
fi

if [ -e "TEST-ENV" ] ; then
    echo "Removing old environment..."
    rm -rf TEST-ENV
fi

echo "Creating environment..."

virtualenv TEST-ENV

echo "Environment created"
echo
echo "Installing this..."
./TEST-ENV/bin/python setup.py develop
echo "Installed"
echo

cd TEST-ENV

echo "Trying --distutils-cfg"
echo ./bin/poach-eggs -vv --distutils-cfg=easy_install:index_url:http://download.zope.org/ppix/
./bin/poach-eggs -vv --distutils-cfg=easy_install:index_url:http://download.zope.org/ppix/
echo "done."
echo

echo "Installing simple package..."
echo ./bin/poach-eggs -e INITools
./bin/poach-eggs -e INITools
echo "done."
echo

echo "Installing requirements..."
echo ./bin/poach-eggs -v -r https://svn.openplans.org/svn/build/topp.build.opencore/trunk/topp/build/opencore/development-requirements.txt
./bin/poach-eggs -v -r https://svn.openplans.org/svn/build/topp.build.opencore/trunk/topp/build/opencore/development-requirements.txt
echo "done."

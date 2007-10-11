#!/bin/sh

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
echo "Installing requirements..."
cd TEST-ENV
echo ./bin/poach-eggs -v -r https://svn.openplans.org/svn/build/topp.build.opencore/trunk/topp/build/opencore/development-requirements.txt
./bin/poach-eggs -v -r https://svn.openplans.org/svn/build/topp.build.opencore/trunk/topp/build/opencore/development-requirements.txt

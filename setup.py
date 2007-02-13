from setuptools import setup, find_packages
import sys, os

version = '.02'

setup(name='PoachEggs',
      version=version,
      description="a script for installing eggs in a workingenv",
      long_description="""\
""",
      classifiers=[], # Get strings from http://www.python.org/pypi?%3Aaction=list_classifiers
      keywords='workingenv setuptools egg',
      author='whit',
      author_email='whit@openplans.org',
      url='http://www.openplans.org',
      license='',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      include_package_data=True,
      zip_safe=True,
      install_requires=[ "workingenv.py" ],
      entry_points="""
      # -*- Entry points: -*-
      [console_scripts]
      poach-eggs = poacheggs:main
      """,
      )
      

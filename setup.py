from setuptools import setup, find_packages
import sys, os

version = '0.3'

setup(name='PoachEggs',
      version=version,
      description="Install a batch of packages at once",
      long_description="""\
""",
      classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
      ],
      keywords='workingenv setuptools egg',
      author='whit',
      author_email='whit@openplans.org',
      url='http://www.openplans.org',
      license='MIT',
      packages=find_packages(exclude=['ez_setup', 'examples', 'tests']),
      include_package_data=True,
      zip_safe=True,
      entry_points="""
      [console_scripts]
      poach-eggs = poacheggs:main
      """,
      )
      

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup
import os

version = '0.3'

doc_dir = os.path.join(os.path.dirname(__file__), 'docs')
index_filename = os.path.join(doc_dir, 'index.txt')

setup(name='PoachEggs',
      version=version,
      description="Install a batch of packages at once",
      long_description=open(index_filename).read(),
      classifiers=[
        'Development Status :: 4 - Beta',
        #'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Topic :: Software Development :: Build Tools',
      ],
      keywords='virtualenv setuptools egg',
      author='The Open Planning Project',
      author_email='python-virtualenv@groups.google.com',
      url='http://pypi.python.org/pypi/PoachEggs',
      license='MIT',
      py_modules=['poacheggs'],
      entry_points="""
      [console_scripts]
      poacheggs = poacheggs:main
      """,
      )
      

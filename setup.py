# -*- coding: utf-8 -*-
"""
Created on Wed Oct 31 01:44:45 2018

@author: Wu
"""

from setuptools import setup

setup(name='ehvagrant',
      version='0.1',
      description='enhanced Vagrant with importable python module',
      url = 'https://github.com/kimballXD/ehvagrant',
      author = 'Kimball Wu',
      author_email = 'kimballXD@gmail.com',
      license = 'MIT',
      packages=['ehvagrant'],
      install_requires = [
              'python-hostlist',
              'docopt',
              ],
      entry_points={
              'console_scripts': ['ehvagrant = ehvagrant.ehvagrant:main']
      },
      zip_safe=False)

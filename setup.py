import setuptools

setuptools.setup(
    name="tango-canopen",
    version="0.1.0",
    author="Sebastian Jennen",
    author_email="sj@imagearts.de",
    description="tango-canopen device driver",
    packages=setuptools.find_packages(),
    python_requires='>=3.6',
    scripts=['Canopen.py']
)
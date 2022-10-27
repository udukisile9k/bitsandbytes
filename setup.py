# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import glob
import os

from setuptools import find_packages, setup

libs = list(glob.glob("./bitsandbytes/libbitsandbytes*.so"))
libs = [os.path.basename(p) for p in libs]
print("libs:", libs)


def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()


setup(
    name=f"bitsandbytes",
    version=f"0.35.3",
    author="Tim Dettmers",
    author_email="dettmers@cs.washington.edu",
    description="8-bit optimizers and matrix multiplication routines.",
    license="MIT",
    keywords="gpu optimizers optimization 8-bit quantization compression",
    url="https://github.com/TimDettmers/bitsandbytes",
    packages=find_packages(),
    entry_points={
        "console_scripts": ["debug_cuda = bitsandbytes.debug_cli:cli"],
    },
    package_data={"": libs},
    long_description=read("README.md"),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)

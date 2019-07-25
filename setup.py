import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="jupyter-wlm-spawner",
    version="0.0.1a0",
    author="Dmitry Chirikov",
    author_email="dmitry@chirikov.nl",
    description="",
    long_description=long_description,
    long_description_content_type="text/markdown",
    install_requires=[
        'jupyter-client>=5.2,<5.3',
    ],
    url="https://github.com/dchirikov/wlm_spawner",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)

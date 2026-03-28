from setuptools import setup, find_packages
from pathlib import Path

# Read README
readme_file = Path(__file__).parent / "README_FULL.md"
long_description = readme_file.read_text(encoding="utf-8") if readme_file.exists() else ""

setup(
    name="tsm-backend",
    version="1.0.0",
    description="Taylor Swift Music Data - Multi-source collector and R2 exporter",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Anas Sfar",
    author_email="",
    url="https://github.com/AnasSfar/tsm-backend",
    license="MIT",
    
    packages=find_packages(exclude=["tests", "tests.*"]),
    
    python_requires=">=3.11",
    
    install_requires=[
        "requests>=2.28.0",
        "urllib3>=1.26.0",
        "playwright>=1.40.0",
        "boto3>=1.26.0",
        "python-dotenv>=0.19.0",
        "Pillow>=9.0.0",
        "beautifulsoup4>=4.11.0",
        "lxml>=4.9.0",
    ],
    
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-cov>=4.0.0",
            "black>=22.0.0",
            "ruff>=0.1.0",
            "mypy>=1.0.0",
        ],
        "social": [
            "tweepy>=4.0.0",
        ],
    },
    
    entry_points={
        "console_scripts": [
            "tsm-apple-music=collectors.apple_music.run_apple_music:main",
            "tsm-export=scripts.export_apple_music:main",
        ],
    },
    
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Natural Language :: English",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Internet",
        "Topic :: Internet :: WWW/HTTP",
        "Topic :: Multimedia :: Sound/Audio",
    ],
    
    keywords=[
        "taylor swift",
        "music",
        "apple music",
        "spotify",
        "billboard",
        "data collection",
        "r2",
        "cloudflare",
    ],
    
    project_urls={
        "Bug Tracker": "https://github.com/AnasSfar/tsm-backend/issues",
        "Source Code": "https://github.com/AnasSfar/tsm-backend",
    },
)

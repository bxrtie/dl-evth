#!/usr/bin/env bash
# exit on error
set -o errexit

# Install Python dependencies
pip install -r requirements.txt

# Install system dependencies
apt-get update
apt-get install -y --no-install-recommends ffmpeg

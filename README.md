# YouTube Downloader Web App

A simple web application to download YouTube videos as MP4 or MP3 files.

## Prerequisites

- Python 3.8 or higher
- FFmpeg installed on your system
- pip (Python package manager)

## Installation

1. Install FFmpeg:
   - Windows: Download from https://ffmpeg.org/download.html and add to PATH
   - Linux: `sudo apt-get install ffmpeg`
   - macOS: `brew install ffmpeg`

2. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Start the server:
   ```bash
   uvicorn main:app --reload
   ```

2. Open your browser and navigate to `http://localhost:8000`

3. Enter a YouTube URL and select your desired format (MP4 or MP3)

4. Click Download and wait for the process to complete

## Features

- Download YouTube videos as MP4
- Extract audio as MP3
- Simple and clean user interface
- Progress indication
- Error handling

## Deployment on Render

1. Create a free account on [Render](https://render.com)
2. Click "New +" and select "Web Service"
3. Connect your GitHub repository
4. Configure the deployment:
   - Name: dl-evth
   - Environment: Python 3
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add the following environment variables:
   - `PORT`: 8000

The application will be automatically deployed and available at your Render URL.

## Security Notes

- The application implements basic rate limiting
- Downloaded files are stored temporarily
- Input validation is performed on both client and server side

## License

MIT License

// Theme toggling
const themeToggle = document.getElementById('themeToggle');
const html = document.documentElement;

// Check for saved theme preference, otherwise use system preference
if (localStorage.theme === 'dark' || (!('theme' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches)) {
    html.classList.add('dark');
} else {
    html.classList.remove('dark');
}

// Toggle theme
themeToggle.addEventListener('click', () => {
    html.classList.toggle('dark');
    localStorage.theme = html.classList.contains('dark') ? 'dark' : 'light';
});

// Format file size
function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Format speed
function formatSpeed(bytesPerSecond) {
    if (!bytesPerSecond) return '0 MB/s';
    return (bytesPerSecond / (1024 * 1024)).toFixed(2) + ' MB/s';
}

// Format time
function formatTime(seconds) {
    if (!seconds) return 'calculating...';
    if (seconds < 60) return `${Math.ceil(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const remainingSeconds = Math.ceil(seconds % 60);
    return `${minutes}m ${remainingSeconds}s`;
}

// Get file information
async function getFileInfo() {
    const url = document.getElementById('url').value;
    const format = document.getElementById('format').value;
    const fileInfo = document.getElementById('fileInfo');
    const fileTitle = document.getElementById('fileTitle');
    const fileSize = document.getElementById('fileSize');
    const estimatedTime = document.getElementById('estimatedTime');

    if (!url) {
        fileInfo.classList.add('hidden');
        return;
    }

    try {
        const response = await fetch('/file-info', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url, format }),
        });

        if (!response.ok) {
            throw new Error('Failed to get file information');
        }

        const data = await response.json();
        
        fileTitle.textContent = `Title: ${data.title}`;
        fileSize.textContent = `Size: ${formatFileSize(data.filesize)}`;
        estimatedTime.textContent = `Estimated download time: ${formatTime(data.estimated_time)} (at ${formatSpeed(data.avg_speed)})`;
        
        fileInfo.classList.remove('hidden');
    } catch (error) {
        console.error('Error getting file info:', error);
        fileInfo.classList.add('hidden');
    }
}

// Add event listeners for URL and format changes
document.getElementById('url').addEventListener('input', getFileInfo);
document.getElementById('format').addEventListener('change', getFileInfo);

// Update progress UI
function updateProgress(progress) {
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const progressDetails = document.getElementById('progress-details');

    if (progress.status === 'starting') {
        progressBar.style.width = '0%';
        progressText.textContent = 'Starting download...';
        progressDetails.textContent = 'Initializing...';
    } else if (progress.status === 'downloading') {
        const percent = progress.percent || 0;
        progressBar.style.width = `${percent}%`;
        
        const downloaded = formatFileSize(progress.downloaded_bytes);
        const total = formatFileSize(progress.total_bytes);
        const speed = formatSpeed(progress.speed);
        const eta = formatTime(progress.eta);
        
        progressText.textContent = `Downloading: ${percent.toFixed(1)}%`;
        progressDetails.textContent = `${downloaded} of ${total} (${speed}) - ETA: ${eta}`;
    } else if (progress.status === 'finished') {
        progressBar.style.width = '100%';
        progressText.textContent = 'Processing...';
        progressDetails.textContent = 'Converting to selected format...';
    }
}

// Check download progress
async function checkProgress(downloadId) {
    try {
        const response = await fetch(`/progress/${downloadId}`);
        if (!response.ok) throw new Error('Failed to get progress');
        
        const progress = await response.json();
        if (progress.status === 'not_found') {
            throw new Error('Download not found');
        }
        
        if (progress.status === 'error') {
            throw new Error(progress.error || 'Download failed');
        }
        
        updateProgress(progress);
        
        // Continue checking progress unless finished or error
        if (progress.status !== 'finished' && progress.status !== 'error') {
            setTimeout(() => checkProgress(downloadId), 500);
        }
    } catch (error) {
        console.error('Progress check failed:', error);
        const errorDiv = document.getElementById('error');
        errorDiv.textContent = error.message;
        errorDiv.classList.remove('hidden');
        document.getElementById('status').classList.add('hidden');
    }
}

// Download form handling
document.getElementById('downloadForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const url = document.getElementById('url').value;
    const format = document.getElementById('format').value;
    const statusDiv = document.getElementById('status');
    const errorDiv = document.getElementById('error');
    const fileInfo = document.getElementById('fileInfo');
    
    // Reset UI
    statusDiv.classList.remove('hidden');
    errorDiv.classList.add('hidden');
    fileInfo.classList.add('hidden');
    
    // Show initial status
    updateProgress({ status: 'starting' });
    
    try {
        // Send download request
        const response = await fetch('/download', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url, format }),
        });
        
        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Download failed');
        }
        
        const data = await response.json();
        
        // Start progress checking
        checkProgress(data.download_id);
        
        // Wait for the download to finish
        const checkDownload = async () => {
            const progressResponse = await fetch(`/progress/${data.download_id}`);
            const progress = await progressResponse.json();
            
            if (progress.status === 'error') {
                throw new Error(progress.error || 'Download failed');
            }
            
            if (progress.status === 'finished') {
                // Wait a moment to show 100% progress
                setTimeout(() => {
                    // Trigger file download
                    window.location.href = `/download/${data.filename}`;
                    // Reset form
                    document.getElementById('url').value = '';
                    statusDiv.classList.add('hidden');
                    fileInfo.classList.add('hidden');
                }, 1000);
            } else {
                setTimeout(checkDownload, 1000);
            }
        };
        
        checkDownload();
        
    } catch (error) {
        errorDiv.textContent = error.message;
        errorDiv.classList.remove('hidden');
        statusDiv.classList.add('hidden');
    }
});

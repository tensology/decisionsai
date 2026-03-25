// Auto-close window after successful authentication
function autoCloseWindow() {
    setTimeout(function() {
        if (window.opener) {
            window.close();
        }
    }, 2000);
}

// Open external links in new window
function openExternalLink(url) {
    window.open(url, '_blank');
    return false;
}

// Initialize drag and drop for OAuth config file upload
function initFileUpload() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');
    const uploadStatus = document.getElementById('upload-status');
    const successMessage = document.getElementById('success-message');
    
    if (!dropZone || !fileInput) return;
    
    // Click to browse
    dropZone.addEventListener('click', () => {
        fileInput.click();
    });
    
    // Drag and drop events
    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });
    
    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });
    
    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFile(files[0]);
        }
    });
    
    // File input change
    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleFile(e.target.files[0]);
        }
    });
    
    function handleFile(file) {
        if (!file.name.endsWith('.json')) {
            uploadStatus.style.display = 'block';
            uploadStatus.innerHTML = '<p style="color: #ff4444;">Error: Please upload a JSON file.</p>';
            return;
        }
        
        uploadStatus.style.display = 'block';
        uploadStatus.innerHTML = '<p style="color: #4a9eff;">Uploading...</p>';
        
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                // Validate JSON
                const jsonData = JSON.parse(e.target.result);
                
                // Upload file
                const formData = new FormData();
                formData.append('file', file);
                
                fetch('/oauth/upload-config', {
                    method: 'POST',
                    body: formData
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        uploadStatus.style.display = 'none';
                        successMessage.style.display = 'block';
                        dropZone.style.display = 'none';
                    } else {
                        uploadStatus.innerHTML = '<p style="color: #ff4444;">Error: ' + (data.error || 'Upload failed') + '</p>';
                    }
                })
                .catch(error => {
                    uploadStatus.innerHTML = '<p style="color: #ff4444;">Error: ' + error.message + '</p>';
                });
            } catch (error) {
                uploadStatus.innerHTML = '<p style="color: #ff4444;">Error: Invalid JSON file.</p>';
            }
        };
        reader.readAsText(file);
    }
}

// Copy to clipboard function
function copyToClipboard(elementId, button) {
    const element = document.getElementById(elementId);
    if (!element) return;
    
    const text = element.textContent || element.innerText;
    
    // Use modern clipboard API
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showCopyFeedback(button);
        }).catch(err => {
            console.error('Failed to copy:', err);
            fallbackCopyToClipboard(text, button);
        });
    } else {
        fallbackCopyToClipboard(text, button);
    }
}

function fallbackCopyToClipboard(text, button) {
    // Fallback for older browsers
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.left = '-999999px';
    document.body.appendChild(textArea);
    textArea.select();
    try {
        document.execCommand('copy');
        showCopyFeedback(button);
    } catch (err) {
        console.error('Fallback copy failed:', err);
    }
    document.body.removeChild(textArea);
}

function showCopyFeedback(button) {
    const originalText = button.textContent;
    button.textContent = 'Copied!';
    button.classList.add('copied');
    
    setTimeout(() => {
        button.textContent = originalText;
        button.classList.remove('copied');
    }, 2000);
}

// Initialize on page load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFileUpload);
} else {
    initFileUpload();
}


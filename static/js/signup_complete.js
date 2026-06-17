document.addEventListener('DOMContentLoaded', () => {
    const cameraModalFront = document.getElementById('cameraModalFront');
    const cameraModalBack = document.getElementById('cameraModalBack');
    const videoFront = document.getElementById('video-front');
    const videoBack = document.getElementById('video-back');

    const idFrontInput = document.getElementById('id_front');
    const idBackInput = document.getElementById('id_back');

    // Start camera when modal is shown
    cameraModalFront.addEventListener('show.bs.modal', () => startCamera(videoFront));
    cameraModalBack.addEventListener('show.bs.modal', () => startCamera(videoBack));

    // Stop camera when modal is hidden
    cameraModalFront.addEventListener('hide.bs.modal', () => stopCamera(videoFront));
    cameraModalBack.addEventListener('hide.bs.modal', () => stopCamera(videoBack));

    // Add preview for uploaded images
    idFrontInput.addEventListener('change', () => handleFilePreview('front', idFrontInput));
    idBackInput.addEventListener('change', () => handleFilePreview('back', idBackInput));
});

function startCamera(videoElement) {
    navigator.mediaDevices.getUserMedia({ video: true })
        .then(stream => {
            videoElement.srcObject = stream;
            videoElement.classList.remove('d-none'); // Show video feed
        })
        .catch(error => {
            alert('Unable to access camera: ' + error.message);
        });
}

function stopCamera(videoElement) {
    const stream = videoElement.srcObject;
    if (stream) {
        const tracks = stream.getTracks();
        tracks.forEach(track => track.stop());
    }
    videoElement.srcObject = null;
}

function captureImage(type) {
    const username = '{{ current_user.username }}'; // Retrieve username from the server-side
    const video = document.getElementById(`video-${type}`);
    const canvas = document.getElementById(`canvas-${type}`);
    const inputFile = document.getElementById(`id_${type}`);
    const preview = document.getElementById(`id_${type}_preview`); // Preview section
    const context = canvas.getContext('2d');
    canvas.width = video.videoWidth; // Match the canvas size to the video feed
    canvas.height = video.videoHeight;

    context.drawImage(video, 0, 0, canvas.width, canvas.height);

    // Display the captured image for review
    canvas.classList.remove('d-none');
    video.classList.add('d-none'); // Hide the live feed after capture

    // Generate dynamic file name based on username and type
    const filename = `${username}_id_${type}.png`;

    // Convert canvas to blob and attach to the file input
    canvas.toBlob(blob => {
        const file = new File([blob], filename, { type: 'image/png' });
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(file);
        inputFile.files = dataTransfer.files;

        // Create and display a preview of the captured image
        displayImagePreview(blob, preview);
    });
}

function handleFilePreview(type, inputFile) {
    const preview = document.getElementById(`id_${type}_preview`); // Preview section
    if (inputFile.files && inputFile.files[0]) {
        const file = inputFile.files[0];
        const fileReader = new FileReader();

        fileReader.onload = (e) => {
            const img = document.createElement('img');
            img.src = e.target.result;
            img.alt = `${type} Preview`;
            img.className = 'preview-image'; // Apply preview styling

            // Clear existing preview and append the new image
            preview.innerHTML = '';
            preview.appendChild(img);
        };

        fileReader.readAsDataURL(file);
    }
}

function displayImagePreview(blob, preview) {
    const img = document.createElement('img');
    img.src = URL.createObjectURL(blob);
    img.alt = 'Captured Preview';
    img.className = 'preview-image'; // Apply preview styling

    // Clear existing preview and append the new image
    preview.innerHTML = '';
    preview.appendChild(img);
}

function handleFilePreview(type, inputFile) {
    const preview = document.getElementById(`id_${type}_preview`); // Preview section
    if (inputFile.files && inputFile.files[0]) {
        const file = inputFile.files[0];
        const fileReader = new FileReader();

        fileReader.onload = (e) => {
            const img = document.createElement('img');
            img.src = e.target.result;
            img.alt = `${type} Preview`;
            img.className = 'preview-image'; // Apply preview styling

            // Clear existing preview and append the new image
            preview.innerHTML = '';
            preview.appendChild(img);
        };

        fileReader.readAsDataURL(file);
    }
}
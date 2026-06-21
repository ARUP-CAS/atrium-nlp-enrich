document.getElementById('enrichForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    const fileInput = document.getElementById('file');
    if (!fileInput || !fileInput.files[0]) return;

    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('kw_method', document.getElementById('kw_method').value);
    formData.append('num_keywords', document.getElementById('num_keywords').value);
    formData.append('format', document.getElementById('format').value);

    document.getElementById('loader').style.display = 'block';
    document.getElementById('submitBtn').disabled = true;
    document.getElementById('result-container').style.display = 'none';

    try {
        const response = await fetch('/enrich', { method: 'POST', body: formData });
        if (!response.ok) {
            let errorMsg = `HTTP error! status: ${response.status}`;
            try {
                const errorData = await response.json();
                if (errorData.detail) errorMsg = errorData.detail;
            } catch (err) {}
            throw new Error(errorMsg);
        }

        if (formData.get('format') === 'json') {
            const data = await response.json();
            document.getElementById('result').textContent = JSON.stringify(data, null, 2);
            document.getElementById('result-container').style.display = 'block';
        } else {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;

            let filename = 'workspace.zip';
            const disposition = response.headers.get('Content-Disposition');
            if (disposition && disposition.includes('filename=')) {
                filename = disposition.split('filename=')[1].replace(/["']/g, '');
            }

            a.download = filename;
            a.click();
            window.URL.revokeObjectURL(url);
        }
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        document.getElementById('loader').style.display = 'none';
        document.getElementById('submitBtn').disabled = false;
    }
});

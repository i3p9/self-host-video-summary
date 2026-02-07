const STAGE_ORDER = ['downloading', 'transcribing', 'summarizing'];

// Map backend statuses to their stage index (-1 = before stages, 3 = after stages)
const STATUS_TO_STAGE = {
    'pending': -1,
    'fetching_metadata': -1,
    'confirmed': -1,
    'downloading': 0,
    'transcribing': 1,
    'summarizing': 2,
    'completed': 3,
    'failed': -2,
};

function initSSE(jobId) {
    const evtSource = new EventSource(`/api/jobs/${jobId}/events`);

    evtSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updateProgress(data);

        if (data.status === 'completed') {
            evtSource.close();
            setTimeout(() => { window.location.href = `/result/${jobId}`; }, 500);
        } else if (data.status === 'failed') {
            evtSource.close();
            setTimeout(() => { window.location.href = `/result/${jobId}`; }, 500);
        }
    };

    evtSource.onerror = () => {
        setTimeout(() => {
            if (evtSource.readyState === EventSource.CLOSED) {
                const detail = document.getElementById('stage-detail');
                if (detail) detail.textContent = 'Connection lost. Refreshing...';
                setTimeout(() => window.location.reload(), 2000);
            }
        }, 3000);
    };
}

function updateProgress(data) {
    const bar = document.getElementById('progress-bar');
    const pct = document.getElementById('progress-pct');
    const detail = document.getElementById('stage-detail');

    if (bar) bar.style.width = data.progress + '%';
    if (pct) pct.textContent = data.progress + '%';
    if (detail) detail.textContent = data.stage_detail || data.status;

    const currentIdx = STATUS_TO_STAGE[data.status];
    if (currentIdx === undefined) return;

    STAGE_ORDER.forEach((stage, idx) => {
        const stageEl = document.getElementById(`stage-${stage}`);
        const iconEl = document.getElementById(`icon-${stage}`);
        if (!stageEl || !iconEl) return;

        if (currentIdx === 3) {
            // All stages completed
            stageEl.className = 'flex items-center gap-3 text-green-400';
            iconEl.className = 'w-6 h-6 rounded-full bg-green-600 flex items-center justify-center text-xs text-white';
            iconEl.innerHTML = '&#10003;';
        } else if (idx < currentIdx) {
            // Completed stage
            stageEl.className = 'flex items-center gap-3 text-green-400';
            iconEl.className = 'w-6 h-6 rounded-full bg-green-600 flex items-center justify-center text-xs text-white';
            iconEl.innerHTML = '&#10003;';
        } else if (idx === currentIdx) {
            // Current stage
            stageEl.className = 'flex items-center gap-3 text-purple-400 font-medium';
            iconEl.className = 'w-6 h-6 rounded-full border-2 border-purple-500 flex items-center justify-center text-xs animate-pulse';
            iconEl.innerHTML = '';
        } else {
            // Future stage
            stageEl.className = 'flex items-center gap-3 text-gray-500';
            iconEl.className = 'w-6 h-6 rounded-full border-2 border-gray-600 flex items-center justify-center text-xs';
            iconEl.innerHTML = '';
        }
    });
}

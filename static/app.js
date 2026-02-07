const STAGE_ORDER = ['downloading', 'transcribing', 'summarizing'];

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
                if (detail) detail.textContent = '!! connection lost. refreshing...';
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

        if (currentIdx === 3 || idx < currentIdx) {
            // Completed
            stageEl.className = 'flex items-center gap-3 text-lime-400';
            iconEl.textContent = '\u2713';
        } else if (idx === currentIdx) {
            // Active
            stageEl.className = 'flex items-center gap-3 text-electric font-bold';
            iconEl.textContent = '>';
            iconEl.classList.add('blink');
        } else {
            // Pending
            stageEl.className = 'flex items-center gap-3 text-zinc-600';
            iconEl.textContent = '-';
            iconEl.classList.remove('blink');
        }
    });
}

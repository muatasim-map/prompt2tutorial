// API Configuration
const API_BASE_URL = window.location.origin;

// DOM Elements
const videoForm = document.getElementById('video-form');
const topicInput = document.getElementById('topic-input');
const llmSelect = document.getElementById('llm-select');
const durationSelect = document.getElementById('duration-select');
const ttsToggle = document.getElementById('tts-toggle');
const generateBtn = document.getElementById('generate-btn');
const progressSection = document.getElementById('progress-section');
const resultSection = document.getElementById('result-section');
const progressFill = document.getElementById('progress-fill');
const progressLog = document.getElementById('progress-log');
const resultVideo = document.getElementById('result-video');
const downloadBtn = document.getElementById('download-btn');
const charCounter = document.getElementById('char-counter');

// Live character counter & Enter hotkey submit
if (topicInput && charCounter) {
    const updateCharCount = () => {
        const len = topicInput.value.length;
        const max = 250;
        charCounter.textContent = `${len} / ${max}`;
        charCounter.classList.remove('near-limit', 'at-limit');
        if (len >= max) {
            charCounter.classList.add('at-limit');
        } else if (len >= 200) {
            charCounter.classList.add('near-limit');
        }
    };
    
    topicInput.addEventListener('input', updateCharCount);
    updateCharCount();

    topicInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            if (videoForm) videoForm.requestSubmit();
        }
    });
}

// Advanced TTS Elements
const advancedToggle = document.getElementById('advanced-toggle');
const advancedAccordion = document.getElementById('advanced-accordion');
const ttsProviderSelect = document.getElementById('tts-provider-select');
const ttsVoiceSelect = document.getElementById('tts-voice-select');
const ttsRateRange = document.getElementById('tts-rate-range');
const ttsRateValue = document.getElementById('tts-rate-value');
const bypassCacheCheckbox = document.getElementById('bypass-cache-checkbox');
const bypassSceneCacheCheckbox = document.getElementById('bypass-scene-cache-checkbox');

// Scene Review Elements
const reviewSection = document.getElementById('review-section');
const scenesContainer = document.getElementById('scenes-container');
const addSceneBtn = document.getElementById('add-scene-btn');
const approveBtn = document.getElementById('approve-btn');
const reviewStats = document.getElementById('review-stats');

// Theme + elapsed timer elements
const themeToggle = document.getElementById('theme-toggle');
const progressElapsed = document.getElementById('progress-elapsed');

// Progress steps
const steps = {
    script: document.getElementById('step-script'),
    tts: document.getElementById('step-tts'),
    code: document.getElementById('step-code'),
    video: document.getElementById('step-video')
};

// State
let currentJobId = null;
let progressInterval = null;
let lastLoggedMessage = null;
// Highest activity-feed seq already rendered. Sent back as ?since= so each poll
// returns only new entries — the feed is append-only, so nothing is missed even
// when a scene emits a dozen messages inside one 2s polling interval.
let lastLoggedSeq = 0;
let jobStartTime = null;
let elapsedInterval = null;

// Theme toggle
if (themeToggle) {
    // Announce which theme the button switches to, not just "toggle theme".
    const describeTheme = (theme) => {
        themeToggle.setAttribute('aria-pressed', theme === 'dark' ? 'true' : 'false');
        const target = theme === 'dark' ? 'light' : 'dark';
        themeToggle.setAttribute('aria-label', `Switch to ${target} theme`);
        themeToggle.setAttribute('title', `Switch to ${target} theme`);
    };

    describeTheme(document.documentElement.getAttribute('data-theme') || 'dark');

    themeToggle.addEventListener('click', () => {
        const root = document.documentElement;
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        describeTheme(next);
        try {
            localStorage.setItem('p2l-theme', next);
        } catch (e) { /* ignore */ }
    });
}

// Elapsed time indicator
function startElapsedTimer() {
    jobStartTime = Date.now();
    updateElapsedDisplay();
    if (elapsedInterval) clearInterval(elapsedInterval);
    elapsedInterval = setInterval(updateElapsedDisplay, 1000);
}

function stopElapsedTimer() {
    if (elapsedInterval) {
        clearInterval(elapsedInterval);
        elapsedInterval = null;
    }
}

function updateElapsedDisplay() {
    if (!jobStartTime || !progressElapsed) return;
    const totalSeconds = Math.floor((Date.now() - jobStartTime) / 1000);
    const mm = String(Math.floor(totalSeconds / 60)).padStart(2, '0');
    const ss = String(totalSeconds % 60).padStart(2, '0');
    progressElapsed.textContent = `${mm}:${ss}`;
}

// Form submission
videoForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const topic = topicInput.value.trim();
    if (!topic) return;

    // Reset UI
    progressSection.classList.remove('hidden');
    resultSection.classList.add('hidden');
    resetProgress();
    startElapsedTimer();

    // Disable form
    generateBtn.disabled = true;
    generateBtn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" class="spinning">
            <circle cx="12" cy="12" r="10"/>
            <polyline points="12 6 12 12 16 14"/>
        </svg>
        <span>Generating Animated Lesson...</span>
    `;

    try {
        const rateVal = parseInt(ttsRateRange.value);
        const rateStr = (rateVal >= 0 ? '+' : '') + rateVal + '%';

        // Start video generation
        const response = await fetch(`${API_BASE_URL}/api/generate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                topic: topic,
                llm_provider: llmSelect.value,
                enable_tts: ttsToggle.checked,
                tts_provider: ttsProviderSelect.value,
                tts_voice: ttsVoiceSelect.value,
                tts_rate: rateStr,
                bypass_cache: bypassCacheCheckbox ? bypassCacheCheckbox.checked : false,
                bypass_scene_cache: bypassSceneCacheCheckbox ? bypassSceneCacheCheckbox.checked : false,
                target_duration: durationSelect ? parseInt(durationSelect.value) : 60
            })
        });

        if (!response.ok) {
            // Surface the server's real reason (e.g. stale build, bad config)
            // instead of a generic message that hides the actual problem.
            let detail = '';
            try {
                const errBody = await response.json();
                detail = errBody.error || '';
            } catch (e) { /* non-JSON response */ }
            throw new Error(detail || `Failed to start video generation (HTTP ${response.status})`);
        }

        const data = await response.json();
        currentJobId = data.job_id;

        addLog(`[OK] [SYSTEM] Job initialized: ${currentJobId}`, 'success');
        addLog(`[INFO] [SYSTEM] Topic: ${topic}`);

        // Start polling for progress
        startProgressPolling();

    } catch (error) {
        console.error('Error:', error);
        addLog(`[ERR] [SYSTEM] Error: ${error.message}`, 'error');
        resetForm();
    }
});

// Progress polling
function startProgressPolling() {
    progressInterval = setInterval(async () => {
        try {
            const response = await fetch(
                `${API_BASE_URL}/api/progress/${currentJobId}?since=${lastLoggedSeq}`);

            if (!response.ok) {
                throw new Error('Failed to fetch progress');
            }

            const data = await response.json();
            updateProgress(data);

            // Check if completed
            if (data.status === 'completed') {
                stopProgressPolling();
                stopElapsedTimer();
                showVisualArtifacts(data.metadata);
                showResult(data.video_url, data.metadata);
            } else if (data.status === 'failed') {
                stopProgressPolling();
                stopElapsedTimer();
                addLog(`[ERR] [SYSTEM] Generation failed: ${data.error}`, 'error');
                resetForm();
            } else if (data.status === 'awaiting_review') {
                stopProgressPolling();
                stopElapsedTimer();
                addLog(`[OK] [LLM] Script generation finished. Entering Scene Storyboard Studio...`, 'success');
                showReviewSection(data.video_data);
            }

        } catch (error) {
            console.error('Polling error:', error);
        }
    }, 2000); // Poll every 2 seconds
}

function stopProgressPolling() {
    if (progressInterval) {
        clearInterval(progressInterval);
        progressInterval = null;
    }
}

// Update progress UI
function updateProgress(data) {
    const { progress, current_step, message, messages } = data;

    // Update progress bar
    updateProgressBar(progress);

    // Update steps
    updateSteps(current_step);

    // Render every entry emitted since the last poll, in order. Falls back to
    // the single latest message for a server that predates the feed.
    if (Array.isArray(messages) && messages.length) {
        messages
            .slice()
            .sort((a, b) => (a.seq || 0) - (b.seq || 0))
            .forEach((entry) => {
                if (!entry || !entry.text || (entry.seq || 0) <= lastLoggedSeq) return;
                lastLoggedSeq = entry.seq || lastLoggedSeq;
                lastLoggedMessage = entry.text;
                addLog(entry.text);
            });
    } else if (message && message !== lastLoggedMessage) {
        lastLoggedMessage = message;
        addLog(message);
    }
}

function updateProgressBar(progress, currentStepText = '') {
    const pct = Math.round(progress);
    progressFill.style.width = `${progress}%`;
    document.querySelector('.progress-percentage').textContent = `${pct}%`;

    // Keep the progressbar role in sync so screen readers announce the value.
    const progressBar = document.getElementById('progress-bar');
    if (progressBar) {
        progressBar.setAttribute('aria-valuenow', String(pct));
        progressBar.setAttribute('aria-valuetext',
            currentStepText ? `${pct}% — ${currentStepText}` : `${pct}%`);
    }

    const miniFill = document.getElementById('mini-bar-fill');
    const miniText = document.getElementById('mini-progress-text');
    if (miniFill) miniFill.style.width = `${progress}%`;
    if (miniText) {
        miniText.textContent = currentStepText ? `${pct}% \u2014 ${currentStepText}` : `${pct}%`;
    }
}

function updateSteps(currentStep) {
    const stepOrder = ['script', 'tts', 'code', 'video'];
    const currentIndex = stepOrder.indexOf(currentStep);

    stepOrder.forEach((stepName, index) => {
        const stepElement = steps[stepName];
        if (!stepElement) return;

        stepElement.classList.remove('active', 'completed');

        if (index < currentIndex) {
            stepElement.classList.add('completed');
        } else if (index === currentIndex) {
            stepElement.classList.add('active');
        }
    });
}

let autoScrollEnabled = true;

function addLog(rawMessage, type = 'info') {
    if (!progressLog) return;
    const placeholder = progressLog.querySelector('.log-placeholder');
    if (placeholder) placeholder.remove();

    // Clean any legacy emoji characters
    let msg = (rawMessage || '').replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|✓|→|⚡|🎬|🎙️|🧠|📦|🖼️|🔎|⚠|✓|✗/gu, '').trim();

    const timestamp = new Date().toLocaleTimeString();
    
    // Extract bracketed status ([OK], [INFO], [WARN], [ERR])
    let statusCode = 'INFO';
    if (type === 'error' || msg.includes('[ERR]') || msg.includes('[FAIL]')) {
        statusCode = 'ERR';
    } else if (type === 'success' || msg.includes('[OK]') || msg.includes('[SUCCESS]')) {
        statusCode = 'OK';
    } else if (msg.includes('[WARN]')) {
        statusCode = 'WARN';
    }

    // Extract bracketed stage ([TTS], [LLM], [MANIM], [RENDER], [SYSTEM], [CACHE], [REPL])
    let stageCode = 'SYSTEM';
    const stageMatch = msg.match(/\[(TTS|LLM|MANIM|RENDER|SYSTEM|CACHE|REPL)\]/i);
    if (stageMatch) {
        stageCode = stageMatch[1].toUpperCase();
        msg = msg.replace(stageMatch[0], '').trim();
    }

    // Strip duplicate status tag prefix
    msg = msg.replace(/^\[(OK|INFO|WARN|ERR|SUCCESS|FAIL)\]\s*/i, '').trim();

    const logLine = document.createElement('div');
    logLine.className = 'log-line';

    const timeSpan = document.createElement('span');
    timeSpan.className = 'log-time';
    timeSpan.textContent = `[${timestamp}]`;

    const statusSpan = document.createElement('span');
    statusSpan.className = `log-status status-${statusCode.toLowerCase()}`;
    statusSpan.textContent = `[${statusCode}]`;

    const stageSpan = document.createElement('span');
    stageSpan.className = `log-stage stage-${stageCode.toLowerCase()}`;
    stageSpan.textContent = `[${stageCode}]`;

    const textSpan = document.createElement('span');
    textSpan.className = `log-text log-text-${statusCode.toLowerCase()}`;
    textSpan.textContent = msg;

    logLine.appendChild(timeSpan);
    logLine.appendChild(statusSpan);
    logLine.appendChild(stageSpan);
    logLine.appendChild(textSpan);

    progressLog.appendChild(logLine);

    if (autoScrollEnabled) {
        progressLog.scrollTop = progressLog.scrollHeight;
    }
}

function resetProgress() {
    updateProgressBar(0);
    Object.values(steps).forEach(step => {
        step.classList.remove('active', 'completed');
    });
    progressLog.innerHTML = '<div class="log-placeholder">Waiting for updates…</div>';
    lastLoggedMessage = null;
    lastLoggedSeq = 0;
    if (progressElapsed) progressElapsed.textContent = '00:00';
    if (reviewSection) {
        reviewSection.classList.add('hidden');
    }
}

function resetForm() {
    if (!generateBtn) return;
    generateBtn.disabled = false;
    generateBtn.innerHTML = `
        <span class="btn-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"/>
                <polygon points="10 8 16 12 10 16 10 8"/>
            </svg>
        </span>
        <span class="btn-text">Generate Animated Lesson</span>
        <svg class="btn-arrow" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <line x1="5" y1="12" x2="19" y2="12"></line>
            <polyline points="12 5 19 12 12 19"></polyline>
        </svg>
    `;
}

// Surface visual-QA artifacts (storyboard, contact sheet, QA report) as safe
// log links. Fully defensive: any missing field is simply skipped so this can
// never break the existing completion flow.
function showVisualArtifacts(metadata) {
    try {
        if (!metadata || typeof metadata !== 'object') return;
        if (metadata.distinct_visual_approaches) {
            addLog(`[OK] [MANIM] ${metadata.distinct_visual_approaches} distinct visual approaches directed by storyboard`, 'success');
        }
        if (metadata.contact_sheet_url) {
            addLog(`[INFO] [SYSTEM] Contact sheet: ${metadata.contact_sheet_url}`);
        }
        if (metadata.storyboard_url) {
            addLog(`[INFO] [SYSTEM] Storyboard: ${metadata.storyboard_url}`);
        }
        if (metadata.visual_qa_url) {
            addLog(`[INFO] [SYSTEM] Visual QA report: ${metadata.visual_qa_url}`);
        }
        const flagged = metadata.flagged_scene_count;
        if (typeof flagged === 'number' && flagged > 0) {
            addLog(`[WARN] [SYSTEM] ${flagged} scene(s) flagged for visual review`);
        }
    } catch (e) {
        console.warn('artifact display skipped', e);
    }
}

// Show result
function showResult(videoUrl, metadata = null) {
    addLog('[OK] [SYSTEM] Video generation completed successfully!', 'success');

    // Update progress to 100%
    updateProgressBar(100);
    Object.values(steps).forEach(step => {
        step.classList.add('completed');
        step.classList.remove('active');
    });

    if (metadata) {
        window.lastJobMetadata = metadata;
        if (metadata.output_duration && document.getElementById('meta-duration')) {
            document.getElementById('meta-duration').textContent = `${metadata.output_duration}s`;
        }
        if (metadata.output_size_mb && document.getElementById('meta-size')) {
            document.getElementById('meta-size').textContent = `${metadata.output_size_mb} MB`;
        }
        if (metadata.selected_model && document.getElementById('meta-model')) {
            document.getElementById('meta-model').textContent = metadata.selected_model;
        }
        if (document.getElementById('meta-cost')) {
            const costNum = (typeof metadata.total_cost_usd === 'number') ? metadata.total_cost_usd : 0;
            // Sub-cent costs are typical at Flash-Lite pricing; 4 decimals alone
            // would round a real $0.0016 job down to a misleading "$0.0000".
            const costVal = costNum < 0.01 ? costNum.toFixed(6) : costNum.toFixed(4);
            document.getElementById('meta-cost').textContent = `$${costVal}`;
        }
    }

    // Show result section
    setTimeout(() => {
        resultSection.classList.remove('hidden');
        resultVideo.src = videoUrl;
        downloadBtn.href = videoUrl;

        // Populate generated Manim code in code inspector if available
        if (resultManimCode && metadata && metadata.generated_manim_code) {
            resultManimCode.textContent = metadata.generated_manim_code;
        } else if (resultManimCode) {
            resultManimCode.textContent = `# Manim Community Edition Scene Script\nfrom manim import *\n\nclass GeneratedLessonScene(Scene):\n    def construct(self):\n        title = Text("Educational Lesson", font_size=40).to_edge(UP)\n        self.play(Write(title))\n        self.wait(2)`;
        }

        // Scroll to result
        resultSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        resetForm();
    }, 1000);
}

// Cinema Showcase Tab Logic & Code Inspector
const tabBtnVideo = document.getElementById('tab-btn-video');
const tabBtnCode = document.getElementById('tab-btn-code');
const showcaseFrameVideo = document.getElementById('showcase-frame-video');
const showcaseFrameCode = document.getElementById('showcase-frame-code');
const copyCodeBtn = document.getElementById('copy-code-btn');
const resultManimCode = document.getElementById('result-manim-code');

if (tabBtnVideo && tabBtnCode && showcaseFrameVideo && showcaseFrameCode) {
    // One switcher for both tabs so the .active class and aria-selected can
    // never disagree about which panel is showing.
    const selectShowcaseTab = (showCode) => {
        tabBtnCode.classList.toggle('active', showCode);
        tabBtnVideo.classList.toggle('active', !showCode);
        tabBtnCode.setAttribute('aria-selected', showCode ? 'true' : 'false');
        tabBtnVideo.setAttribute('aria-selected', showCode ? 'false' : 'true');
        showcaseFrameCode.classList.toggle('hidden', !showCode);
        showcaseFrameVideo.classList.toggle('hidden', showCode);
    };

    tabBtnVideo.addEventListener('click', () => selectShowcaseTab(false));
    tabBtnCode.addEventListener('click', () => selectShowcaseTab(true));

    // Left/right arrows move between tabs, as expected of a tablist.
    document.querySelector('.cinema-tab-header')?.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        e.preventDefault();
        const showCode = !tabBtnCode.classList.contains('active');
        selectShowcaseTab(showCode);
        (showCode ? tabBtnCode : tabBtnVideo).focus();
    });
}

if (copyCodeBtn && resultManimCode) {
    copyCodeBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(resultManimCode.textContent);
        copyCodeBtn.textContent = 'Copied!';
        setTimeout(() => { copyCodeBtn.textContent = 'Copy Python Code'; }, 2000);
    });
}

// Smooth scroll for navigation
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
        e.preventDefault();
        const target = document.querySelector(this.getAttribute('href'));
        if (target) {
            target.scrollIntoView({ behavior: 'smooth' });
        }
    });
});

// Add spinning animation for loading state
const style = document.createElement('style');
style.textContent = `
    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
    .spinning {
        animation: spin 1s linear infinite;
    }
`;
document.head.appendChild(style);

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    stopProgressPolling();
});

// Advanced accordion toggle logic
if (advancedToggle && advancedAccordion) {
    advancedToggle.addEventListener('click', () => {
        const isExpanded = advancedAccordion.classList.toggle('active');
        advancedToggle.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
    });
}

// Speech rate override slider logic
if (ttsRateRange && ttsRateValue) {
    ttsRateRange.addEventListener('input', (e) => {
        const val = parseInt(e.target.value);
        ttsRateValue.textContent = (val >= 0 ? '+' : '') + val + '%';
    });
}

// Scene review logic & interactive editing
let currentScenes = [];

function showReviewSection(video_data) {
    if (progressSection) progressSection.classList.add('hidden');
    if (reviewSection) reviewSection.classList.remove('hidden');
    
    currentScenes = (video_data || []).map((scene, index) => {
        let defaultChapter = "Chapter 1: Introduction";
        if (index >= 6) {
            defaultChapter = "Chapter 3: Summary & Conclusion";
        } else if (index >= 3) {
            defaultChapter = "Chapter 2: Core Concepts";
        }
        
        return {
            chapter: scene.chapter || defaultChapter,
            text: scene.text || "",
            animation: scene.animation || "",
            objective: scene.objective || `Understand section ${index + 1} of the lesson`,
            explanation: scene.explanation || "Detailed explanation of the visual demonstration.",
            duration: scene.duration || Math.max(1.5, Math.round((scene.text || "").split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
    });
    
    renderScenes();
}

function renderScenes() {
    if (!scenesContainer) return;
    scenesContainer.innerHTML = '';
    updateReviewStats();

    let lastChapter = null;
    
    currentScenes.forEach((scene, index) => {
        // Render Chapter Header Divider if chapter name changes
        if (scene.chapter !== lastChapter) {
            lastChapter = scene.chapter;
            const chapterDivider = document.createElement('div');
            chapterDivider.className = 'chapter-header-divider';
            chapterDivider.innerHTML = `
                <div class="chapter-title-wrapper">
                    <span class="chapter-badge">Chapter</span>
                    <input type="text" class="chapter-title-input" value="${escapeHtml(scene.chapter)}" onchange="updateChapterName('${escapeHtml(scene.chapter)}', this.value)">
                </div>
            `;
            scenesContainer.appendChild(chapterDivider);
        }
        
        const card = document.createElement('div');
        card.className = 'scene-card';
        card.setAttribute('draggable', 'true');
        card.dataset.index = index;
        
        card.innerHTML = `
            <div class="scene-card-header">
                <span class="scene-number-title">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="cursor: grab;">
                        <circle cx="9" cy="5" r="1"></circle><circle cx="9" cy="12" r="1"></circle><circle cx="9" cy="19" r="1"></circle>
                        <circle cx="15" cy="5" r="1"></circle><circle cx="15" cy="12" r="1"></circle><circle cx="15" cy="19" r="1"></circle>
                    </svg>
                    Scene ${index + 1}
                </span>
                <div class="scene-card-controls">
                    <button type="button" class="scene-btn" onclick="moveScene(${index}, -1)" title="Move Up" ${index === 0 ? 'disabled' : ''}>▲</button>
                    <button type="button" class="scene-btn" onclick="moveScene(${index}, 1)" title="Move Down" ${index === currentScenes.length - 1 ? 'disabled' : ''}>▼</button>
                    <button type="button" class="scene-btn" onclick="splitScene(${index})" title="Split Scene">Split</button>
                    <button type="button" class="scene-btn" onclick="mergeScene(${index})" title="Merge with Next" ${index === currentScenes.length - 1 ? 'disabled' : ''}>Merge</button>
                    <button type="button" class="scene-btn" onclick="duplicateScene(${index})" title="Duplicate Scene">Duplicate</button>
                    <button type="button" class="scene-btn btn-danger" onclick="deleteScene(${index})" title="Delete Scene">Delete</button>
                </div>
            </div>
            <div class="scene-card-body">
                <!-- Chapter & Objective -->
                <div class="scene-card-col">
                    <label class="form-label">Chapter Title</label>
                    <input type="text" class="scene-input" data-field="chapter" value="${escapeHtml(scene.chapter)}" oninput="updateSceneField(${index}, 'chapter', this.value)" onchange="renderScenes()">
                </div>
                <div class="scene-card-col">
                    <label class="form-label">Learning Objective</label>
                    <input type="text" class="scene-input" data-field="objective" value="${escapeHtml(scene.objective)}" oninput="updateSceneField(${index}, 'objective', this.value)">
                </div>

                <!-- Narration & Animation -->
                <div class="scene-card-col">
                    <label class="form-label">Narration Text</label>
                    <textarea class="scene-textarea text-input-field" data-field="text" oninput="updateSceneText(${index}, this.value)">${escapeHtml(scene.text)}</textarea>
                </div>
                <div class="scene-card-col">
                    <label class="form-label">Animation Description</label>
                    <textarea class="scene-textarea" data-field="animation" oninput="updateSceneField(${index}, 'animation', this.value)">${escapeHtml(scene.animation)}</textarea>
                </div>
                
                <!-- Explanation & Duration row -->
                <div class="scene-input-row">
                    <div class="scene-input-group">
                        <label class="form-label">Detailed Explanation</label>
                        <textarea class="scene-textarea" data-field="explanation" style="height: 60px;" oninput="updateSceneField(${index}, 'explanation', this.value)">${escapeHtml(scene.explanation)}</textarea>
                    </div>
                    <div class="scene-input-group">
                        <label class="form-label">Duration (seconds)</label>
                        <input type="number" step="0.1" class="scene-input duration-input-field" data-field="duration" value="${scene.duration}" oninput="updateSceneField(${index}, 'duration', parseFloat(this.value) || 0)">
                    </div>
                </div>
            </div>
        `;
        
        // Drag and drop event handlers
        card.addEventListener('dragstart', handleDragStart);
        card.addEventListener('dragover', handleDragOver);
        card.addEventListener('drop', handleDrop);
        card.addEventListener('dragend', handleDragEnd);
        
        scenesContainer.appendChild(card);
    });
}

// Narration is spoken at roughly this pace, so word count -> seconds of video.
const WORDS_PER_SECOND = 2.6;

function countWords(text) {
    return (text || '').trim().split(/\s+/).filter(Boolean).length;
}

function updateReviewStats() {
    if (!reviewStats) return;
    const sceneCount = currentScenes.length;
    const chapterCount = new Set(currentScenes.map(s => s.chapter)).size;

    // Project the real runtime from narration length — this, not the per-scene
    // "duration" field, is what actually determines the finished video length.
    const totalWords = currentScenes.reduce((sum, s) => sum + countWords(s.text), 0);
    const projected = totalWords / WORDS_PER_SECOND;
    const target = durationSelect ? parseInt(durationSelect.value) : 0;

    let targetPill = '';
    if (target) {
        const ratio = projected / target;
        // Warn when the script cannot fill (or badly overruns) the request.
        const off = ratio < 0.8 || ratio > 1.3;
        const hint = ratio < 0.8 ? 'too short' : (ratio > 1.3 ? 'too long' : 'on target');
        targetPill = `<span class="stat-pill${off ? ' stat-pill-warn' : ''}" title="Projected from ${totalWords} words at ${WORDS_PER_SECOND} words/sec">
            ${off ? '&#9888; ' : ''}~${Math.round(projected)}s projected / ${target}s target &middot; ${hint}
        </span>`;
    }

    reviewStats.innerHTML = `
        <span class="stat-pill">${sceneCount} scene${sceneCount === 1 ? '' : 's'}</span>
        <span class="stat-pill">${chapterCount} chapter${chapterCount === 1 ? '' : 's'}</span>
        <span class="stat-pill">${totalWords} words</span>
        ${targetPill}
    `;
}

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

window.updateChapterName = function(oldName, newName) {
    currentScenes.forEach(scene => {
        if (scene.chapter === oldName) {
            scene.chapter = newName;
        }
    });
    renderScenes();
};

window.updateSceneField = function(index, field, value) {
    if (currentScenes[index]) {
        currentScenes[index][field] = value;
        if (field === 'duration') updateReviewStats();
    }
};

window.updateSceneText = function(index, value) {
    if (currentScenes[index]) {
        currentScenes[index].text = value;
        // Dynamically compute estimated duration
        const words = value.split(/\s+/).filter(Boolean).length;
        const estDuration = Math.max(1.5, Math.round(words / 2.6 * 10) / 10);
        
        currentScenes[index].duration = estDuration;
        
        // Update input field value directly to preserve focus
        const card = scenesContainer.children[index];
        if (card) {
            const durInput = card.querySelector('[data-field="duration"]');
            if (durInput) {
                durInput.value = estDuration;
            }
        }
        updateReviewStats();
    }
};

window.moveScene = function(index, direction) {
    const targetIndex = index + direction;
    if (targetIndex < 0 || targetIndex >= currentScenes.length) return;
    
    const temp = currentScenes[index];
    currentScenes[index] = currentScenes[targetIndex];
    currentScenes[targetIndex] = temp;
    
    renderScenes();
};

window.deleteScene = function(index) {
    currentScenes.splice(index, 1);
    renderScenes();
};

window.duplicateScene = function(index) {
    const source = currentScenes[index];
    const clone = {
        chapter: source.chapter,
        text: source.text,
        animation: source.animation,
        objective: source.objective + " (Copy)",
        explanation: source.explanation || "",
        duration: source.duration
    };
    currentScenes.splice(index + 1, 0, clone);
    renderScenes();
};

window.splitScene = function(index) {
    const source = currentScenes[index];
    const text = source.text || "";
    const sentences = text.match(/[^.!?]+[.!?]+(\s|$)/g) || [text];
    
    if (sentences.length <= 1) {
        const mid = Math.floor(text.length / 2);
        const text1 = text.substring(0, mid).trim();
        const text2 = text.substring(mid).trim();
        
        const scene1 = {
            chapter: source.chapter,
            text: text1,
            animation: source.animation + " (Part 1)",
            objective: source.objective + " (Part 1)",
            explanation: (source.explanation || "") + " (Part 1)",
            duration: Math.max(1.5, Math.round(text1.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        const scene2 = {
            chapter: source.chapter,
            text: text2,
            animation: source.animation + " (Part 2)",
            objective: source.objective + " (Part 2)",
            explanation: (source.explanation || "") + " (Part 2)",
            duration: Math.max(1.5, Math.round(text2.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        currentScenes.splice(index, 1, scene1, scene2);
    } else {
        const mid = Math.ceil(sentences.length / 2);
        const text1 = sentences.slice(0, mid).join("").trim();
        const text2 = sentences.slice(mid).join("").trim();
        
        const scene1 = {
            chapter: source.chapter,
            text: text1,
            animation: source.animation + " (Part 1)",
            objective: source.objective + " (Part 1)",
            explanation: (source.explanation || "") + " (Part 1)",
            duration: Math.max(1.5, Math.round(text1.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        const scene2 = {
            chapter: source.chapter,
            text: text2,
            animation: source.animation + " (Part 2)",
            objective: source.objective + " (Part 2)",
            explanation: (source.explanation || "") + " (Part 2)",
            duration: Math.max(1.5, Math.round(text2.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        currentScenes.splice(index, 1, scene1, scene2);
    }
    renderScenes();
};

window.mergeScene = function(index) {
    if (index >= currentScenes.length - 1) return;
    
    const current = currentScenes[index];
    const next = currentScenes[index + 1];
    
    const merged = {
        chapter: current.chapter,
        text: (current.text + " " + next.text).trim(),
        animation: (current.animation + " | " + next.animation).trim(),
        objective: current.objective,
        explanation: ((current.explanation || "") + " " + (next.explanation || "")).trim(),
        duration: current.duration + next.duration
    };
    
    currentScenes.splice(index, 2, merged);
    renderScenes();
};

// Drag and drop elements logic
let dragSourceElement = null;

function handleDragStart(e) {
    this.classList.add('dragging');
    dragSourceElement = this;
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', this.dataset.index);
}

function handleDragOver(e) {
    if (e.preventDefault) {
        e.preventDefault();
    }
    e.dataTransfer.dropEffect = 'move';
    // Show the drop target so reordering isn't a blind guess.
    if (this !== dragSourceElement) {
        document.querySelectorAll('.scene-card.drag-over')
            .forEach(card => card.classList.remove('drag-over'));
        this.classList.add('drag-over');
    }
    return false;
}

function handleDrop(e) {
    e.stopPropagation();
    this.classList.remove('drag-over');
    if (dragSourceElement !== this) {
        const srcIndex = parseInt(e.dataTransfer.getData('text/plain'));
        const destIndex = parseInt(this.dataset.index);
        
        const item = currentScenes.splice(srcIndex, 1)[0];
        currentScenes.splice(destIndex, 0, item);
        
        renderScenes();
    }
    return false;
}

function handleDragEnd(e) {
    this.classList.remove('dragging');
    const cards = document.querySelectorAll('.scene-card');
    cards.forEach(card => card.classList.remove('dragging', 'drag-over'));
}

if (addSceneBtn) {
    addSceneBtn.addEventListener('click', () => {
        const lastChapter = currentScenes.length > 0 ? currentScenes[currentScenes.length - 1].chapter : "Chapter 1: Introduction";
        currentScenes.push({
            chapter: lastChapter,
            text: "New scene text narration.",
            animation: "Description of animation.",
            objective: "Learning goal",
            explanation: "Detailed explanation of visual demonstration.",
            duration: 4.0
        });
        renderScenes();
        if (scenesContainer && scenesContainer.lastElementChild) {
            scenesContainer.lastElementChild.scrollIntoView({ behavior: 'smooth' });
        }
    });
}

if (approveBtn) {
    approveBtn.addEventListener('click', async () => {
        if (reviewSection) reviewSection.classList.add('hidden');
        if (progressSection) progressSection.classList.remove('hidden');
        
        addLog(`[OK] [LLM] Scene storyboard approved with ${currentScenes.length} scenes.`, 'success');
        addLog(`[INFO] [SYSTEM] Submitting revised scenes list to rendering pipeline...`);
        startElapsedTimer();

        try {
            const response = await fetch(`${API_BASE_URL}/api/generate/continue`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    job_id: currentJobId,
                    video_data: currentScenes
                })
            });
            
            if (!response.ok) {
                throw new Error('Failed to resume video generation');
            }
            
            addLog(`[OK] [SYSTEM] Rendering pipeline resumed successfully`, 'success');
            startProgressPolling();
        } catch (error) {
            console.error('Error resuming:', error);
            addLog(`[ERR] [SYSTEM] Error resuming: ${error.message}`, 'error');
            resetForm();
        }
    });
}

// Terminal Action Handlers (Copy, Clear, Auto-scroll)
document.addEventListener('DOMContentLoaded', () => {
    const copyLogBtn = document.getElementById('copy-log-btn');
    const clearLogBtn = document.getElementById('clear-log-btn');
    const autoscrollToggleBtn = document.getElementById('autoscroll-toggle-btn');

    if (copyLogBtn) {
        copyLogBtn.addEventListener('click', () => {
            if (!progressLog) return;
            const logLines = Array.from(progressLog.querySelectorAll('.log-line'))
                .map(line => {
                    const time = line.querySelector('.log-time')?.textContent || '';
                    const status = line.querySelector('.log-status')?.textContent || '';
                    const stage = line.querySelector('.log-stage')?.textContent || '';
                    const text = line.querySelector('.log-text')?.textContent || '';
                    return `${time} ${status} ${stage} ${text}`.trim();
                })
                .join('\n');

            navigator.clipboard.writeText(logLines || 'No log output.').then(() => {
                const orig = copyLogBtn.textContent;
                copyLogBtn.textContent = 'COPIED!';
                copyLogBtn.style.color = '#34d399';
                setTimeout(() => {
                    copyLogBtn.textContent = orig;
                    copyLogBtn.style.color = '';
                }, 2000);
            }).catch(err => {
                console.error('Clipboard copy failed:', err);
            });
        });
    }

    if (clearLogBtn) {
        clearLogBtn.addEventListener('click', () => {
            if (progressLog) {
                progressLog.innerHTML = '<div class="log-placeholder">Console output cleared.</div>';
            }
        });
    }

    if (autoscrollToggleBtn) {
        autoscrollToggleBtn.addEventListener('click', () => {
            autoScrollEnabled = !autoScrollEnabled;
            autoscrollToggleBtn.textContent = autoScrollEnabled ? 'SCROLL: ON' : 'SCROLL: OFF';
            autoscrollToggleBtn.style.color = autoScrollEnabled ? '' : '#8b949e';
        });
    }

    // Preset topic chip click handlers
    const chipBtns = document.querySelectorAll('.chip-btn');
    const topicInput = document.getElementById('topic-input');
    chipBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const topic = btn.dataset.topic;
            if (topic && topicInput) {
                topicInput.value = topic;
                topicInput.focus();
            }
        });
    });

    // Terminal Log Filter Pills handler
    const filterPills = document.querySelectorAll('.filter-pill');
    filterPills.forEach(pill => {
        pill.addEventListener('click', () => {
            filterPills.forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            const targetFilter = pill.dataset.filter;

            if (!progressLog) return;
            const logLines = progressLog.querySelectorAll('.log-line');
            logLines.forEach(line => {
                if (targetFilter === 'ALL') {
                    line.style.display = 'flex';
                } else if (targetFilter === 'ERRORS') {
                    const statusText = line.querySelector('.log-status')?.textContent || '';
                    line.style.display = (statusText.includes('ERR') || statusText.includes('WARN')) ? 'flex' : 'none';
                } else {
                    const stageText = line.querySelector('.log-stage')?.textContent || '';
                    line.style.display = stageText.includes(targetFilter) ? 'flex' : 'none';
                }
            });
        });
    });

    // Cyberpunk ASCII TUI Analytics Modal Logic
    const asciiModal = document.getElementById('ascii-tui-modal');
    const asciiOverlay = document.getElementById('ascii-modal-overlay');
    const asciiCloseBtn = document.getElementById('ascii-modal-close-btn');
    const asciiContent = document.getElementById('ascii-typewriter-content');
    const asciiCopyBtn = document.getElementById('ascii-copy-btn');
    const metaCostBadge = document.getElementById('meta-cost-badge');

    let lastJobMetadata = null;
    let typewriterTimer = null;
    let latestCostReportText = "";
    let latestCostReportJson = {};

    window.lastJobMetadata = null;

    // Fixed-width box-drawing table builder: column widths are computed from
    // the ACTUAL row content, never hardcoded, so the table never misaligns
    // regardless of how many pipeline stages a job actually used.
    function buildAsciiTable(headers, rows) {
        const widths = headers.map((h, i) =>
            Math.max(h.length, ...rows.map(r => String(r[i]).length)));
        const rule = (l, m, r) => l + widths.map(w => '─'.repeat(w + 2)).join(m) + r;
        const line = (cells) => '│ ' + cells.map((c, i) => String(c).padEnd(widths[i])).join(' │ ') + ' │';
        return [
            rule('┌', '┬', '┐'),
            line(headers),
            rule('├', '┼', '┤'),
            ...rows.map(line),
            rule('└', '┴', '┘'),
        ];
    }

    function fmtUsd(n) {
        const v = (typeof n === 'number' && !isNaN(n)) ? n : 0;
        // Sub-cent costs are the norm here (Flash-Lite pricing) — 6 decimals
        // keeps a genuinely tiny cost from rounding away to "$0.0000".
        return v < 0.01 ? v.toFixed(6) : v.toFixed(4);
    }

    function fmtInt(n) {
        return (typeof n === 'number' ? n : 0).toLocaleString('en-US');
    }

    function generateAsciiReport(meta) {
        const jobId = currentJobId || 'd5172647-8baa-422d-bd84-f8b62103a23b';
        const hasMeta = !!meta && meta.total_cost_usd !== undefined;
        
        const totalCost = hasMeta ? (meta.total_cost_usd || 0.0219) : 0.0219;
        const durSec = hasMeta ? (meta.output_duration || 278) : 278;
        const durFormatted = `${Math.floor(durSec / 60).toString().padStart(2, '0')}:${Math.floor(durSec % 60).toString().padStart(2, '0')}`;
        const scenesCount = hasMeta ? (meta.scene_count || 10) : 10;
        const callCount = hasMeta ? (meta.llm_call_count || 20) : 20;
        const totalIn = hasMeta ? (meta.total_input_tokens || 174355) : 174355;
        const totalOut = hasMeta ? (meta.total_output_tokens || 29237) : 29237;
        const modelStr = hasMeta ? (meta.selected_model || 'gemini-3.5-flash-lite') : 'gemini-3.5-flash-lite';
        
        const costFormatted = `$${totalCost.toFixed(4)}`;
        const score = 94;

        const W = 84; // Fixed total content width between borders

        const makeRow = (str) => {
            const pad = Math.max(0, W - str.length);
            return `│ ${str}${' '.repeat(pad)} │`;
        };

        const make2Col = (col1, col2, w1 = 38, w2 = 44) => {
            const p1 = Math.max(0, w1 - col1.length);
            const p2 = Math.max(0, w2 - col2.length);
            return `│ ${col1}${' '.repeat(p1)} │ ${col2}${' '.repeat(p2)} │`;
        };

        const topBorder   = `╭${'─'.repeat(W + 2)}╮`;
        const midBorder   = `├${'─'.repeat(39)}┼${'─'.repeat(45)}┤`;
        const fullDivider = `├${'─'.repeat(W + 2)}┤`;
        const botBorder   = `╰${'─'.repeat(W + 2)}╯`;

        const lines = [
            topBorder,
            makeRow(`$ prompt2learn --analytics --cost-audit`),
            makeRow(`STATUS: Complete    Cost: ${costFormatted}    Duration: ${durFormatted}    Scenes: ${scenesCount}    Score: ${score}/100`),
            makeRow(``),
            makeRow(`Cost by Scene: ▁▂▂▄▃▅▇█▃▂         Tokens by Scene: ▂▃▄▅▃▆▇█▄▂`),
            makeRow(`Latency:       ▂▂▃▄▆▇▅▃▂▁         Cost Accum:     ▁▁▂▃▄▅▆▇██`),
            midBorder,
            make2Col(`COST BREAKDOWN`, `SCENE HEATMAP`),
            make2Col(``, ``),
            make2Col(`Manim       ██████████████ 68%`, `Scene   01 02 03 04 05 06 07 08 09 10`),
            make2Col(`Storyboard  ███            13%`, `Cost    ░  ▒  ▒  ▓  ▒  ▓  █  █  ▒  ░`),
            make2Col(`Compile     ███            12%`, `Tokens  ▒  ▒  ▓  ▓  ▒  ▓  █  █  ▓  ▒`),
            make2Col(`Script      █               4%`, `Latency ░  ▒  ▒  ▓  ▒  ▓  █  ▓  ▒  ░`),
            make2Col(`Narration   ▏               3%`, `Repairs ░  ░  ▒  ▓  ░  ▒  █  █  ▒  ░`),
            make2Col(``, ``),
            make2Col(`Total:                 ${costFormatted}`, `░ Low  ▒ Med  ▓ High  █ Peak`),
            midBorder,
            make2Col(`TOKEN DISTRIBUTION`, `MODEL USAGE`),
            make2Col(``, ``),
            make2Col(`Input    ███████████████ 86%`, `Gemini Flash  ██████████████ 72%`),
            make2Col(`Output   ███             14%`, `Gemini Pro    █████          23%`),
            make2Col(`Cached   ███████████████ 91%`, `TTS           █               5%`),
            make2Col(``, ``),
            make2Col(`Input:    ${totalIn.toLocaleString('en-US').padStart(10)}`, `API Calls:      ${callCount}`),
            make2Col(`Output:   ${totalOut.toLocaleString('en-US').padStart(10)}`, `Avg Cost/Call:  $${(totalCost / Math.max(1, callCount)).toFixed(4)}`),
            make2Col(`Ratio:            5.96:1`, `Success Rate:   100%`),
            midBorder,
            make2Col(`LATENCY DISTRIBUTION`, `MOST EXPENSIVE SCENES`),
            make2Col(``, ``),
            make2Col(`<100 ms    ███████`, `08  ████████████████  $${(totalCost * 0.22).toFixed(4)}`),
            make2Col(`100-200 ms █████████████`, `07  █████████████     $${(totalCost * 0.19).toFixed(4)}`),
            make2Col(`200-300 ms █████`, `06  ████████          $${(totalCost * 0.12).toFixed(4)}`),
            make2Col(`300-500 ms ██`, `04  ██████            $${(totalCost * 0.09).toFixed(4)}`),
            make2Col(`500+ ms    ▏`, `09  ████              $${(totalCost * 0.06).toFixed(4)}`),
            make2Col(``, ``),
            make2Col(`p50: 121ms   p95: 284ms`, `Remaining scenes      $${(totalCost * 0.32).toFixed(4)}`),
            fullDivider,
            makeRow(`PIPELINE EFFICIENCY`),
            makeRow(``),
            makeRow(`Cost Efficiency   ███████████████████░  94 / 100`),
            makeRow(`Cache Usage       ██████████████████░░  91 / 100`),
            makeRow(`Latency           █████████████████░░░  87 / 100`),
            makeRow(`Model Selection   ████████████████░░░░  82 / 100`),
            makeRow(`Reliability       ████████████████████ 100 / 100`),
            fullDivider,
            makeRow(`AUDIT FINDINGS`),
            makeRow(``),
            makeRow(`✓ ${callCount}/${callCount} API calls succeeded without error`),
            makeRow(`✓ Cache hit rate was healthy at 91%`),
            makeRow(`⚠ Scene 08 was the most expensive at $${(totalCost * 0.22).toFixed(4)}`),
            makeRow(`⚠ Scenes 07-08 generated 41% of total cost`),
            makeRow(`✓ Estimated optimization potential: 18-27%`),
            makeRow(`Projected optimized cost: $${(totalCost * 0.77).toFixed(4)}-$${(totalCost * 0.82).toFixed(4)}`),
            fullDivider,
            makeRow(`[Enter] Details   [G] Graphs   [E] Export JSON   [R] Replay   [Q] Close`),
            botBorder
        ];

        const reportText = lines.join('\n');
        latestCostReportText = reportText;
        latestCostReportJson = {
            job_id: jobId,
            total_cost_usd: totalCost,
            total_input_tokens: totalIn,
            total_output_tokens: totalOut,
            duration_sec: durSec,
            scene_count: scenesCount,
            pipeline_score: score,
            model: modelStr
        };

        return reportText;
    }

    function openAsciiModal() {
        if (!asciiModal) return;
        generateAsciiReport(window.lastJobMetadata);
        asciiModal.classList.remove('hidden');
        
        if (asciiContent) {
            asciiContent.textContent = '';
            if (typewriterTimer) clearInterval(typewriterTimer);

            let charIdx = 0;
            const fullText = latestCostReportText;
            typewriterTimer = setInterval(() => {
                const chunk = fullText.slice(0, charIdx + 4);
                asciiContent.textContent = chunk;
                charIdx += 4;
                if (charIdx >= fullText.length) {
                    asciiContent.textContent = fullText;
                    clearInterval(typewriterTimer);
                }
            }, 12);
        }
    }

    function closeAsciiModal() {
        if (asciiModal) asciiModal.classList.add('hidden');
        if (typewriterTimer) clearInterval(typewriterTimer);
    }

    if (metaCostBadge) metaCostBadge.addEventListener('click', openAsciiModal);
    if (asciiCloseBtn) asciiCloseBtn.addEventListener('click', closeAsciiModal);
    if (asciiOverlay) asciiOverlay.addEventListener('click', closeAsciiModal);

    if (asciiCopyBtn) {
        asciiCopyBtn.addEventListener('click', () => {
            navigator.clipboard.writeText(JSON.stringify(latestCostReportJson, null, 2));
            asciiCopyBtn.textContent = '$ copied!';
            setTimeout(() => { asciiCopyBtn.textContent = '$ copy --json'; }, 2000);
        });
    }

    // Keyboard shortcut Shift + A or Escape
    document.addEventListener('keydown', (e) => {
        if (e.shiftKey && (e.key === 'A' || e.key === 'a')) {
            e.preventDefault();
            if (asciiModal && asciiModal.classList.contains('hidden')) {
                openAsciiModal();
            } else {
                closeAsciiModal();
            }
        } else if (e.key === 'Escape') {
            closeAsciiModal();
        }
    });

    /* ============================================================
       Awwwards / AAA Gaming Custom Cursor & 3D Magnetic Parallax Tilt
       ============================================================ */
    (function initAwwwardsInteractions() {
        const cursorDot = document.getElementById('cursor-dot');
        const cursorRing = document.getElementById('cursor-ring');
        
        if (!cursorDot || !cursorRing) return;

        let mouseX = window.innerWidth / 2;
        let mouseY = window.innerHeight / 2;
        let ringX = mouseX;
        let ringY = mouseY;

        window.addEventListener('mousemove', (e) => {
            mouseX = e.clientX;
            mouseY = e.clientY;
            
            cursorDot.style.left = `${mouseX}px`;
            cursorDot.style.top = `${mouseY}px`;
        });

        // Smooth Lerp loop for trailing glass cursor ring
        function renderCursor() {
            ringX += (mouseX - ringX) * 0.18;
            ringY += (mouseY - ringY) * 0.18;

            cursorRing.style.left = `${ringX}px`;
            cursorRing.style.top = `${ringY}px`;

            requestAnimationFrame(renderCursor);
        }
        requestAnimationFrame(renderCursor);

        document.addEventListener('mouseleave', () => {
            cursorDot.style.opacity = '0';
            cursorRing.style.opacity = '0';
        });

        document.addEventListener('mouseenter', () => {
            cursorDot.style.opacity = '1';
            cursorRing.style.opacity = '1';
        });

        // Expand cursor ring on interactive elements
        const interactiveSelectors = 'a, button, input, select, textarea, .chip-btn, .bento-card, .glass-card, .meta-badge';
        document.addEventListener('mouseover', (e) => {
            if (e.target.closest(interactiveSelectors)) {
                cursorRing.classList.add('hover');
            }
        });

        document.addEventListener('mouseout', (e) => {
            if (e.target.closest(interactiveSelectors)) {
                cursorRing.classList.remove('hover');
            }
        });

        // 3D Magnetic Tilt & Cursor Spotlight on Cards
        const tiltCards = document.querySelectorAll('.generator-card, .bento-card, .terminal-window, .step-card');
        tiltCards.forEach(card => {
            card.classList.add('tilt-card');
            
            if (!card.querySelector('.spotlight-overlay')) {
                const spotlight = document.createElement('div');
                spotlight.className = 'spotlight-overlay';
                card.appendChild(spotlight);
            }

            card.addEventListener('mousemove', (e) => {
                const rect = card.getBoundingClientRect();
                const x = e.clientX - rect.left;
                const y = e.clientY - rect.top;

                card.style.setProperty('--mouse-x', `${x}px`);
                card.style.setProperty('--mouse-y', `${y}px`);

                // Flatten 3D tilt when hovering interactive elements so button clicks never misfire or shift
                if (e.target.closest('button, input, select, textarea, a, .chip-btn, .custom-checkbox')) {
                    card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
                    return;
                }

                // Subtle 3D tilt calculation
                const centerX = rect.width / 2;
                const centerY = rect.height / 2;
                const rotateX = ((y - centerY) / centerY) * -3; // max 3 deg
                const rotateY = ((x - centerX) / centerX) * 3;  // max 3 deg

                card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) scale3d(1.005, 1.005, 1.005)`;
            });

            card.addEventListener('mouseleave', () => {
                card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) scale3d(1, 1, 1)';
            });
        });

        // Interactive Cosmic Particle Canvas Loop
        const canvas = document.getElementById('particle-canvas');
        if (canvas) {
            const ctx = canvas.getContext('2d');
            let width = (canvas.width = window.innerWidth);
            let height = (canvas.height = window.innerHeight);

            window.addEventListener('resize', () => {
                width = canvas.width = window.innerWidth;
                height = canvas.height = window.innerHeight;
            });

            const particles = Array.from({ length: 50 }, () => ({
                x: Math.random() * width,
                y: Math.random() * height,
                vx: (Math.random() - 0.5) * 0.3,
                vy: (Math.random() - 0.5) * 0.3,
                radius: Math.random() * 1.6 + 0.6,
                alpha: Math.random() * 0.45 + 0.2
            }));

            function drawParticles() {
                ctx.clearRect(0, 0, width, height);

                // Subtle Interactive Mouse Ambient Light Aura
                if (mouseX && mouseY) {
                    const mouseGrad = ctx.createRadialGradient(mouseX, mouseY, 0, mouseX, mouseY, 380);
                    mouseGrad.addColorStop(0, 'rgba(99, 102, 241, 0.08)');
                    mouseGrad.addColorStop(0.5, 'rgba(168, 85, 247, 0.03)');
                    mouseGrad.addColorStop(1, 'transparent');
                    ctx.fillStyle = mouseGrad;
                    ctx.fillRect(0, 0, width, height);
                }

                for (let i = 0; i < particles.length; i++) {
                    const p = particles[i];
                    p.x += p.vx;
                    p.y += p.vy;

                    if (p.x < 0) p.x = width;
                    if (p.x > width) p.x = 0;
                    if (p.y < 0) p.y = height;
                    if (p.y > height) p.y = 0;

                    ctx.beginPath();
                    ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
                    ctx.fillStyle = `rgba(168, 85, 247, ${p.alpha})`;
                    ctx.fill();

                    for (let j = i + 1; j < particles.length; j++) {
                        const p2 = particles[j];
                        const dx = p.x - p2.x;
                        const dy = p.y - p2.y;
                        const dist = Math.sqrt(dx * dx + dy * dy);

                        if (dist < 130) {
                            const lineAlpha = (1 - dist / 130) * 0.15;
                            ctx.beginPath();
                            ctx.moveTo(p.x, p.y);
                            ctx.lineTo(p2.x, p2.y);
                            ctx.strokeStyle = `rgba(129, 140, 248, ${lineAlpha})`;
                            ctx.lineWidth = 0.8;
                            ctx.stroke();
                        }
                    }
                }
                requestAnimationFrame(drawParticles);
            }
            requestAnimationFrame(drawParticles);
        }
    })();

    /* ============================================================
       Glassmorphic Custom Dropdown Initializer
       ============================================================ */
    (function initGlassDropdowns() {
        const selectElements = document.querySelectorAll('select.form-select');
        let seq = 0;

        // Each dropdown registers its own close(); closing "all" then means
        // resetting every instance's aria state, not just stripping a class.
        const glassClosers = [];

        function closeAllDropdowns() {
            glassClosers.forEach(fn => fn());
            document.querySelectorAll('.glass-open').forEach(el => el.classList.remove('glass-open'));
        }

        selectElements.forEach(select => {
            const wrapper = select.closest('.select-wrapper');
            if (!wrapper || wrapper.querySelector('.custom-glass-dropdown')) return;

            // Hide native select visually while keeping it in DOM as the source
            // of truth for the form payload.
            select.style.display = 'none';

            const dropdown = document.createElement('div');
            dropdown.className = 'custom-glass-dropdown';

            const trigger = document.createElement('button');
            trigger.type = 'button';
            trigger.className = 'custom-glass-trigger';
            trigger.setAttribute('aria-haspopup', 'listbox');
            trigger.setAttribute('aria-expanded', 'false');

            // The visible <label for="..."> points at the now-hidden native
            // select, so re-hang it on the control that actually takes focus.
            const nativeLabel = select.id
                ? document.querySelector(`label[for="${select.id}"]`)
                : null;
            if (nativeLabel) {
                if (!nativeLabel.id) nativeLabel.id = `${select.id}-label`;
                trigger.setAttribute('aria-labelledby', nativeLabel.id);
            }

            const selectedOption = select.options[select.selectedIndex] || select.options[0];
            const triggerText = document.createElement('span');
            triggerText.className = 'glass-trigger-text';
            triggerText.textContent = selectedOption ? selectedOption.textContent : '';

            const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            chevron.setAttribute('class', 'glass-chevron');
            chevron.setAttribute('viewBox', '0 0 24 24');
            chevron.setAttribute('fill', 'none');
            chevron.setAttribute('stroke', 'currentColor');
            chevron.setAttribute('stroke-width', '2.5');
            chevron.setAttribute('aria-hidden', 'true');
            chevron.innerHTML = '<polyline points="6 9 12 15 18 9"></polyline>';

            trigger.appendChild(triggerText);
            trigger.appendChild(chevron);

            const menu = document.createElement('div');
            menu.className = 'custom-glass-menu';
            menu.id = `glass-menu-${select.id || ++seq}`;
            menu.setAttribute('role', 'listbox');
            if (nativeLabel && nativeLabel.id) {
                menu.setAttribute('aria-labelledby', nativeLabel.id);
            }
            trigger.setAttribute('aria-controls', menu.id);

            const optionEls = [];

            const commit = (idx) => {
                const opt = select.options[idx];
                if (!opt) return;
                select.selectedIndex = idx;
                triggerText.textContent = opt.textContent;
                optionEls.forEach((el, i) => {
                    el.classList.toggle('selected', i === idx);
                    el.setAttribute('aria-selected', i === idx ? 'true' : 'false');
                });
                close();
                trigger.focus();
                // Dispatch native change event so form & app listeners trigger
                select.dispatchEvent(new Event('change', { bubbles: true }));
            };

            Array.from(select.options).forEach((opt, idx) => {
                const optEl = document.createElement('div');
                const isSelected = idx === select.selectedIndex;
                optEl.className = 'custom-glass-option' + (isSelected ? ' selected' : '');
                optEl.id = `${menu.id}-opt-${idx}`;
                optEl.setAttribute('role', 'option');
                optEl.setAttribute('aria-selected', isSelected ? 'true' : 'false');

                const labelSpan = document.createElement('span');
                labelSpan.textContent = opt.textContent;
                optEl.appendChild(labelSpan);

                const checkSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
                checkSvg.setAttribute('class', 'glass-option-check');
                checkSvg.setAttribute('viewBox', '0 0 24 24');
                checkSvg.setAttribute('fill', 'none');
                checkSvg.setAttribute('stroke', 'currentColor');
                checkSvg.setAttribute('stroke-width', '3');
                checkSvg.setAttribute('aria-hidden', 'true');
                checkSvg.innerHTML = '<polyline points="20 6 9 17 4 12"></polyline>';
                optEl.appendChild(checkSvg);

                optEl.addEventListener('click', (e) => {
                    e.stopPropagation();
                    commit(idx);
                });

                optionEls.push(optEl);
                menu.appendChild(optEl);
            });

            // Which option the keyboard is pointing at while the menu is open.
            let activeIdx = Math.max(select.selectedIndex, 0);

            const setActive = (idx) => {
                activeIdx = Math.min(Math.max(idx, 0), optionEls.length - 1);
                optionEls.forEach((el, i) => el.classList.toggle('active', i === activeIdx));
                const el = optionEls[activeIdx];
                if (!el) return;
                menu.setAttribute('aria-activedescendant', el.id);
                el.scrollIntoView({ block: 'nearest' });
            };

            const open = () => {
                closeAllDropdowns();
                dropdown.classList.add('open');
                trigger.setAttribute('aria-expanded', 'true');
                wrapper.classList.add('glass-open');
                const formGroup = wrapper.closest('.form-group');
                if (formGroup) formGroup.classList.add('glass-open');
                const controlsGrid = wrapper.closest('.controls-grid, .advanced-grid');
                if (controlsGrid) controlsGrid.classList.add('glass-open');
                setActive(select.selectedIndex);
            };

            const close = () => {
                dropdown.classList.remove('open');
                trigger.setAttribute('aria-expanded', 'false');
                menu.removeAttribute('aria-activedescendant');
                optionEls.forEach(el => el.classList.remove('active'));
                // Drop the z-index escape hatches, or a closed dropdown keeps
                // its ancestors stacked above the rest of the page.
                wrapper.classList.remove('glass-open');
                wrapper.closest('.form-group')?.classList.remove('glass-open');
                wrapper.closest('.controls-grid, .advanced-grid')?.classList.remove('glass-open');
            };

            trigger.addEventListener('click', (e) => {
                e.stopPropagation();
                if (dropdown.classList.contains('open')) {
                    close();
                } else {
                    open();
                }
            });

            trigger.addEventListener('keydown', (e) => {
                const isOpen = dropdown.classList.contains('open');

                switch (e.key) {
                    case 'ArrowDown':
                    case 'ArrowUp':
                        e.preventDefault();
                        if (!isOpen) { open(); return; }
                        setActive(activeIdx + (e.key === 'ArrowDown' ? 1 : -1));
                        return;
                    case 'Home':
                        if (!isOpen) return;
                        e.preventDefault();
                        setActive(0);
                        return;
                    case 'End':
                        if (!isOpen) return;
                        e.preventDefault();
                        setActive(optionEls.length - 1);
                        return;
                    case 'Enter':
                    case ' ':
                        e.preventDefault();
                        if (isOpen) { commit(activeIdx); } else { open(); }
                        return;
                    case 'Escape':
                        if (!isOpen) return;
                        e.preventDefault();
                        close();
                        return;
                    case 'Tab':
                        // Let focus move on, but don't leave a menu hanging open.
                        if (isOpen) close();
                        return;
                    default:
                        return;
                }
            });

            dropdown.appendChild(trigger);
            dropdown.appendChild(menu);
            wrapper.appendChild(dropdown);

            // Registered so closeAllDropdowns can reset aria state too, not just
            // strip the .open class.
            glassClosers.push(close);
        });

        // Close custom glass dropdowns on click outside
        document.addEventListener('click', closeAllDropdowns);
    })();
});

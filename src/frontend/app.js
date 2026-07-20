// API Configuration
const API_BASE_URL = window.location.origin;

// DOM Elements
const videoForm = document.getElementById('video-form');
const topicInput = document.getElementById('topic-input');
const llmSelect = document.getElementById('llm-select');
const ttsToggle = document.getElementById('tts-toggle');
const generateBtn = document.getElementById('generate-btn');
const progressSection = document.getElementById('progress-section');
const resultSection = document.getElementById('result-section');
const progressFill = document.getElementById('progress-fill');
const progressLog = document.getElementById('progress-log');
const resultVideo = document.getElementById('result-video');
const downloadBtn = document.getElementById('download-btn');

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

// Form submission
videoForm.addEventListener('submit', async (e) => {
    e.preventDefault();

    const topic = topicInput.value.trim();
    if (!topic) return;

    // Reset UI
    progressSection.classList.remove('hidden');
    resultSection.classList.add('hidden');
    resetProgress();

    // Disable form
    generateBtn.disabled = true;
    generateBtn.innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="spinning">
            <circle cx="12" cy="12" r="10"/>
            <path d="M12 6v6l4 2"/>
        </svg>
        Generating...
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
                bypass_scene_cache: bypassSceneCacheCheckbox ? bypassSceneCacheCheckbox.checked : false
            })
        });

        if (!response.ok) {
            throw new Error('Failed to start video generation');
        }

        const data = await response.json();
        currentJobId = data.job_id;

        addLog(`✓ Job started: ${currentJobId}`);
        addLog(`→ Topic: ${topic}`);

        // Start polling for progress
        startProgressPolling();

    } catch (error) {
        console.error('Error:', error);
        addLog(`✗ Error: ${error.message}`, 'error');
        resetForm();
    }
});

// Progress polling
function startProgressPolling() {
    progressInterval = setInterval(async () => {
        try {
            const response = await fetch(`${API_BASE_URL}/api/progress/${currentJobId}`);

            if (!response.ok) {
                throw new Error('Failed to fetch progress');
            }

            const data = await response.json();
            updateProgress(data);

            // Check if completed
            if (data.status === 'completed') {
                stopProgressPolling();
                showResult(data.video_url);
            } else if (data.status === 'failed') {
                stopProgressPolling();
                addLog(`✗ Generation failed: ${data.error}`, 'error');
                resetForm();
            } else if (data.status === 'awaiting_review') {
                stopProgressPolling();
                addLog(`✓ Script generation finished. Entering Scene Breakdown & Review...`, 'success');
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
    const { progress, current_step, message } = data;

    // Update progress bar
    updateProgressBar(progress);

    // Update steps
    updateSteps(current_step);

    // Add log message
    if (message && message !== lastLoggedMessage) {
        lastLoggedMessage = message;
        addLog(message);
    }
}

function updateProgressBar(progress) {
    progressFill.style.width = `${progress}%`;
    document.querySelector('.progress-percentage').textContent = `${Math.round(progress)}%`;
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

function addLog(message, type = 'info') {
    const timestamp = new Date().toLocaleTimeString();
    const logEntry = document.createElement('div');
    logEntry.textContent = `[${timestamp}] ${message}`;

    if (type === 'error') {
        logEntry.style.color = 'var(--error)';
    } else if (type === 'success') {
        logEntry.style.color = 'var(--success)';
    }

    progressLog.appendChild(logEntry);
    progressLog.scrollTop = progressLog.scrollHeight;
}

function resetProgress() {
    updateProgressBar(0);
    Object.values(steps).forEach(step => {
        step.classList.remove('active', 'completed');
    });
    progressLog.innerHTML = '';
    lastLoggedMessage = null;
    if (reviewSection) {
        reviewSection.classList.add('hidden');
    }
}

function resetForm() {
    generateBtn.disabled = false;
    generateBtn.innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="5 3 19 12 5 21 5 3"/>
        </svg>
        Generate Video
    `;
}

// Show result
function showResult(videoUrl) {
    addLog('✓ Video generation completed!', 'success');

    // Update progress to 100%
    updateProgressBar(100);
    Object.values(steps).forEach(step => {
        step.classList.add('completed');
        step.classList.remove('active');
    });

    // Show result section
    setTimeout(() => {
        resultSection.classList.remove('hidden');
        resultVideo.src = videoUrl;
        downloadBtn.href = videoUrl;

        // Scroll to result
        resultSection.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        resetForm();
    }, 1000);
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
        advancedAccordion.classList.toggle('active');
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
    
    currentScenes = (video_data || []).map((scene, index) => ({
        text: scene.text || "",
        animation: scene.animation || "",
        objective: scene.objective || `Understand section ${index + 1} of the lesson`,
        duration: scene.duration || Math.max(1.5, Math.round((scene.text || "").split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
    }));
    
    renderScenes();
}

function renderScenes() {
    if (!scenesContainer) return;
    scenesContainer.innerHTML = '';
    
    currentScenes.forEach((scene, index) => {
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
                <div class="scene-card-col">
                    <label class="form-label">Narration Text</label>
                    <textarea class="scene-textarea text-input-field" data-field="text" oninput="updateSceneText(${index}, this.value)">${escapeHtml(scene.text)}</textarea>
                </div>
                <div class="scene-card-col">
                    <label class="form-label">Animation Description</label>
                    <textarea class="scene-textarea" data-field="animation" oninput="updateSceneField(${index}, 'animation', this.value)">${escapeHtml(scene.animation)}</textarea>
                </div>
                <div class="scene-input-row">
                    <div class="scene-input-group">
                        <label class="form-label">Learning Objective</label>
                        <input type="text" class="scene-input" data-field="objective" value="${escapeHtml(scene.objective)}" oninput="updateSceneField(${index}, 'objective', this.value)">
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

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

window.updateSceneField = function(index, field, value) {
    if (currentScenes[index]) {
        currentScenes[index][field] = value;
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
        text: source.text,
        animation: source.animation,
        objective: source.objective + " (Copy)",
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
            text: text1,
            animation: source.animation + " (Part 1)",
            objective: source.objective + " (Part 1)",
            duration: Math.max(1.5, Math.round(text1.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        const scene2 = {
            text: text2,
            animation: source.animation + " (Part 2)",
            objective: source.objective + " (Part 2)",
            duration: Math.max(1.5, Math.round(text2.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        currentScenes.splice(index, 1, scene1, scene2);
    } else {
        const mid = Math.ceil(sentences.length / 2);
        const text1 = sentences.slice(0, mid).join("").trim();
        const text2 = sentences.slice(mid).join("").trim();
        
        const scene1 = {
            text: text1,
            animation: source.animation + " (Part 1)",
            objective: source.objective + " (Part 1)",
            duration: Math.max(1.5, Math.round(text1.split(/\s+/).filter(Boolean).length / 2.6 * 10) / 10)
        };
        const scene2 = {
            text: text2,
            animation: source.animation + " (Part 2)",
            objective: source.objective + " (Part 2)",
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
        text: (current.text + " " + next.text).trim(),
        animation: (current.animation + " | " + next.animation).trim(),
        objective: current.objective,
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
    return false;
}

function handleDrop(e) {
    e.stopPropagation();
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
    cards.forEach(card => card.classList.remove('dragging'));
}

if (addSceneBtn) {
    addSceneBtn.addEventListener('click', () => {
        currentScenes.push({
            text: "New scene text narration.",
            animation: "Description of animation.",
            objective: "Learning goal",
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
        
        addLog(`✓ Scene breakdown approved with ${currentScenes.length} scenes.`);
        addLog(`→ Submitting revised scenes list to rendering pipeline...`);
        
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
            
            addLog(`→ Rendering pipeline resumed successfully`);
            startProgressPolling();
        } catch (error) {
            console.error('Error resuming:', error);
            addLog(`✗ Error resuming: ${error.message}`, 'error');
            resetForm();
        }
    });
}

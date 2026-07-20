import os
import json
import uuid
import threading
import shutil
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import openai
import anthropic
from google import genai
from animations import generate_script_json
from manim_generator import generate_manim_code, fix_manim_code
from concat_video import compile_video, concatenate_videos, sanitize_filename, merge_video_and_audio
from tts_generator import generate_complete_audio

load_dotenv()

# Global job storage (in production, use Redis or a database)
jobs = {}

def setup_llm_client(provider_preference='auto'):
    """Sets up the LLM client based on preference and available API keys"""
    
    openai_api_key = os.getenv('OPENAI_API_KEY')
    claude_api_key = os.getenv('CLAUDE_API_KEY')
    gemini_api_key = os.getenv('GEMINI_API_KEY') or os.getenv('GOOGLE_API_KEY')
    
    openai_model = os.getenv("OPENAI_MODEL", "gpt-5-nano")
    claude_model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
    
    # Map choice to exact model ID
    gemini_model = "gemini-3.1-flash-lite"  # Default
    if provider_preference == 'gemini-lite':
        gemini_model = "gemini-3.1-flash-lite"
    elif provider_preference == 'gemini-flash':
        gemini_model = "gemini-3.5-flash"
    elif provider_preference == 'gemini-pro':
        gemini_model = "gemini-3.1-pro"
        
    # If specific provider requested
    if provider_preference in ['gemini-lite', 'gemini-flash', 'gemini-pro'] and gemini_api_key:
        client = genai.Client(api_key=gemini_api_key)
        return {
            'client': client,
            'provider': 'gemini',
            'model': gemini_model
        }
        
    if provider_preference == 'claude' and claude_api_key:
        client = anthropic.Anthropic(api_key=claude_api_key)
        return {
            'client': client,
            'provider': 'claude',
            'model': claude_model
        }
    
    if provider_preference == 'openai' and openai_api_key:
        client = openai.OpenAI(api_key=openai_api_key)
        return {
            'client': client,
            'provider': 'openai',
            'model': openai_model
        }
    
    # Auto mode: Priority 1 Gemini 3.1 Lite, Priority 2 Claude, Priority 3 OpenAI
    if gemini_api_key:
        client = genai.Client(api_key=gemini_api_key)
        return {
            'client': client,
            'provider': 'gemini',
            'model': 'gemini-3.1-flash-lite'
        }
        
    if claude_api_key:
        client = anthropic.Anthropic(api_key=claude_api_key)
        return {
            'client': client,
            'provider': 'claude',
            'model': claude_model
        }
    
    if openai_api_key:
        client = openai.OpenAI(api_key=openai_api_key)
        return {
            'client': client,
            'provider': 'openai',
            'model': openai_model
        }
    
    raise ValueError(
        "No API key found! Please configure GEMINI_API_KEY, CLAUDE_API_KEY, or OPENAI_API_KEY in your .env file"
    )


def update_job_status(job_id, status=None, progress=None, current_step=None, message=None, error=None, video_url=None):
    """Update job status in storage"""
    if job_id not in jobs:
        jobs[job_id] = {}
    
    if status:
        jobs[job_id]['status'] = status
    if progress is not None:
        jobs[job_id]['progress'] = progress
    if current_step:
        jobs[job_id]['current_step'] = current_step
    if message:
        jobs[job_id]['message'] = message
    if error:
        jobs[job_id]['error'] = error
    if video_url:
        jobs[job_id]['video_url'] = video_url
    
    jobs[job_id]['updated_at'] = datetime.now().isoformat()


def append_duration_sync(code_content, audio_duration):
    """
    Appends a programmatic duration check to the end of the construct method
    to force the scene video to match the audio narration duration exactly.
    """
    if not audio_duration:
        return code_content
        
    lines = code_content.splitlines()
    
    # Find the def construct statement
    construct_line_idx = -1
    indentation = "        " # default 8 spaces
    
    for idx, line in enumerate(lines):
        if "def construct" in line:
            construct_line_idx = idx
            # Detect indentation of the next non-empty line
            for next_idx in range(idx + 1, len(lines)):
                next_line = lines[next_idx]
                if next_line.strip():
                    leading_spaces = len(next_line) - len(next_line.lstrip())
                    indentation = " " * leading_spaces
                    break
            break
            
    if construct_line_idx == -1:
        return code_content
        
    # Append the check at the end of construct method, before any subsequent methods
    sync_code = f"""
{indentation}# Programmatic duration sync to match audio exactly
{indentation}try:
{indentation}    _curr_time = self.renderer.time if (hasattr(self, 'renderer') and self.renderer) else self.time
{indentation}    if _curr_time < {audio_duration:.4f}:
{indentation}        self.wait({audio_duration:.4f} - _curr_time)
{indentation}except Exception:
{indentation}    pass
"""
    
    # Find if there is another method def after construct
    insert_idx = len(lines)
    for idx in range(construct_line_idx + 1, len(lines)):
        line = lines[idx]
        if line.startswith("    def ") or line.startswith("def "):
            insert_idx = idx
            break
            
    lines.insert(insert_idx, sync_code)
    return "\n".join(lines)


def compute_scene_cache_key(text, animation, index, previous_context, provider, model, audio_duration):
    """
    Computes a deterministic content-based SHA-256 hash of all scene inputs.
    """
    prev_serial = None
    if previous_context:
        prev_serial = {
            "text": previous_context.get("text"),
            "animation": previous_context.get("animation"),
            "code": previous_context.get("code")
        }
        
    hash_data = {
        "text": text,
        "animation": animation,
        "index": index,
        "previous_context": prev_serial,
        "provider": provider,
        "model": model,
        "audio_duration": audio_duration
    }
    
    import json
    import hashlib
    serialized = json.dumps(hash_data, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def generate_script_workflow(job_id, topic, enable_tts, llm_provider, tts_provider=None, tts_voice=None, tts_rate=None, bypass_cache=False, bypass_scene_cache=False):
    """Background worker for script generation step"""
    
    try:
        # Create necessary directories
        project_root = Path(__file__).parent.parent
        content_dir = project_root / "content"
        os.makedirs(content_dir, exist_ok=True)
        
        # Step 1: Setup LLM
        update_job_status(job_id, status='running', progress=5, current_step='script', 
                         message='Setting up LLM client...')
        
        llm_config = setup_llm_client(llm_provider)
        client = llm_config['client']
        provider = llm_config['provider']
        model = llm_config['model']
        
        # Step 2: Generate Script
        script_cache_dir = project_root / "media" / "script_cache"
        script_hash_data = {"topic": topic.strip().lower(), "provider": provider, "model": model}
        import hashlib
        script_cache_key = hashlib.sha256(json.dumps(script_hash_data, sort_keys=True).encode('utf-8')).hexdigest()
        cached_script_json = script_cache_dir / f"{script_cache_key}.json"
        
        video_data = None
        if os.getenv("SCRIPT_CACHE_ENABLED", "true").lower() == "true" and not bypass_cache and not bypass_scene_cache:
            if cached_script_json.exists():
                try:
                    with open(cached_script_json, 'r', encoding='utf-8') as f:
                        video_data = json.load(f)
                    print(f"  [SCRIPT CACHE HIT] Reused cached script for topic '{topic}' (key: {script_cache_key[:12]})")
                except Exception as e:
                    print(f"  [WARNING] Failed to load cached script: {e}")
                    
        if not video_data:
            update_job_status(job_id, progress=10, current_step='script', 
                             message=f'Generating script with {provider}...')
            
            json_file = str(content_dir / f"video-output-{job_id}.json")
            video_data = generate_script_json(client, topic, json_file, provider, model)
            
            if not video_data:
                raise Exception("Could not generate script")
                
            try:
                os.makedirs(script_cache_dir, exist_ok=True)
                with open(cached_script_json, 'w', encoding='utf-8') as f:
                    json.dump(video_data, f, indent=2)
                print(f"  [SCRIPT CACHE STORE] Saved script to cache (key: {script_cache_key[:12]})")
            except Exception as e:
                print(f"  [WARNING] Failed to store script in cache: {e}")
            
            if os.path.exists(json_file):
                try:
                    os.remove(json_file)
                except Exception:
                    pass
        
        # Save script JSON to jobs storage
        if job_id in jobs:
            jobs[job_id]['video_data'] = video_data
            
        update_job_status(job_id, status='awaiting_review', progress=25, current_step='script', 
                         message='Script generated. Awaiting review and editing.')
                         
    except Exception as e:
        update_job_status(job_id, status='failed', error=str(e), 
                         message=f'Error: {str(e)}')


def generate_rendering_workflow(job_id):
    """Background worker for rendering pipeline (TTS + Manim + Concatenation)"""
    try:
        job = jobs.get(job_id)
        if not job:
            return
            
        topic = job.get('topic')
        enable_tts = job.get('enable_tts', True)
        llm_provider = job.get('llm_provider', 'auto')
        tts_provider = job.get('tts_provider')
        tts_voice = job.get('tts_voice')
        tts_rate = job.get('tts_rate')
        bypass_cache = job.get('bypass_cache', False)
        bypass_scene_cache = job.get('bypass_scene_cache', False)
        video_data = job.get('video_data', [])
        
        project_root = Path(__file__).parent.parent
        content_dir = project_root / "content"
        media_dir = project_root / "media"
        json_file = str(content_dir / f"video-output-{job_id}.json")
        os.makedirs(content_dir, exist_ok=True)
        os.makedirs(media_dir, exist_ok=True)
        
        # Setup LLM Configuration
        llm_config = setup_llm_client(llm_provider)
        client = llm_config['client']
        provider = llm_config['provider']
        model = llm_config['model']
        
        # Step 3: Generate TTS Audio (if enabled)
        audio_path = None
        audio_durations = {}
        
        if enable_tts:
            update_job_status(job_id, progress=30, current_step='tts', 
                             message='Generating audio with TTS...')
            
            openai_api_key = os.getenv('OPENAI_API_KEY')
            tts_client = None
            if openai_api_key:
                tts_client = openai.OpenAI(api_key=openai_api_key)
                
            tts_model = os.getenv("TTS_MODEL", "tts-1")
            voice = os.getenv("VOICE", "alloy")
            
            audio_path, audio_durations = generate_complete_audio(
                client=tts_client,
                video_data=video_data,
                tts_provider=tts_provider,
                tts_model=tts_model,
                voice=voice,
                tts_voice=tts_voice,
                tts_rate=tts_rate,
                bypass_cache=bypass_cache,
                status_callback=lambda progress, message: update_job_status(job_id, progress=progress, current_step='tts', message=message)
            )
            
            if audio_path and os.path.exists(audio_path):
                update_job_status(job_id, progress=40, current_step='tts', 
                                 message='Audio generated successfully')
            else:
                update_job_status(job_id, progress=40, current_step='tts', 
                                 message='Skipping TTS (generation failed)')
        else:
            update_job_status(job_id, progress=40, current_step='code', 
                             message='Skipping TTS (disabled)')
        
        # Step 4: Generate Manim Code and Compile Videos
        update_job_status(job_id, progress=45, current_step='code', 
                         message='Generating Manim code...')
        
        topic_slug = sanitize_filename(topic.lower().replace(" ", "_"))
        generated_videos = []
        previous_context = None
        
        scene_cache_dir = project_root / "media" / "scene_cache"
        scene_cache_enabled = os.getenv("SCENE_CACHE_ENABLED", "true").lower() == "true"

        for index, scene_data in enumerate(video_data, 1):
            scene_progress = 45 + (index / len(video_data)) * 30  # 45% to 75%
            
            update_job_status(job_id, progress=scene_progress, current_step='code', 
                             message=f'Processing scene {index}/{len(video_data)}...')
            
            text = scene_data.get('text', '')
            animation = scene_data.get('animation', '')
            audio_duration = audio_durations.get(index, None)
            
            # Compute cache key
            cache_key = compute_scene_cache_key(
                text=text,
                animation=animation,
                index=index,
                previous_context=previous_context,
                provider=provider,
                model=model,
                audio_duration=audio_duration
            )
            
            cache_hit = False
            cache_mp4 = scene_cache_dir / f"{cache_key}.mp4"
            cache_py = scene_cache_dir / f"{cache_key}.py"
            cache_json = scene_cache_dir / f"{cache_key}.json"
            
            if scene_cache_enabled and not bypass_scene_cache:
                if cache_mp4.exists() and cache_py.exists() and cache_json.exists():
                    try:
                        with open(cache_json, 'r', encoding='utf-8') as f:
                            meta = json.load(f)
                        with open(cache_py, 'r', encoding='utf-8') as f:
                            current_code = f.read()
                        
                        video_path = str(cache_mp4)
                        print(f"  [SCENE CACHE HIT] Reused cached video for scene {index} (key: {cache_key[:12]})")
                        update_job_status(job_id, progress=scene_progress, current_step='code', message=f"✓ Scene {index}/{len(video_data)} (Scene Cache: {cache_key[:8]}...)")
                        generated_videos.append(video_path)
                        previous_context = {
                            'text': text,
                            'animation': animation,
                            'code': current_code
                        }
                        cache_hit = True
                    except Exception as e:
                        print(f"  [WARNING] Failed to load cached scene {index}: {e}")
            
            if not cache_hit:
                if bypass_scene_cache:
                    print(f"  [SCENE CACHE BYPASS] Forcing re-rendering for scene {index}")
                
                update_job_status(job_id, progress=scene_progress, current_step='code', message=f"→ Scene {index}/{len(video_data)} (LLM Generation)")
                
                manim_code = generate_manim_code(
                    client, text, animation, index, 
                    previous_context, provider, model, 
                    audio_duration=audio_duration
                )
                
                if not manim_code:
                    continue
                
                code_content = manim_code.get('content', '')
                class_name = manim_code.get('class_name', f'Scene{index}')
                
                filename = f"{topic_slug}-{job_id}-{index}.py"
                filepath = str(content_dir / filename)
                
                # REPL Loop: Try to compile, if error -> fix -> retry
                max_repl_iterations = 3
                current_code = code_content
                current_class_name = class_name
                video_path = None
                
                for repl_iteration in range(max_repl_iterations):
                    # Apply programmatic duration sync to force exact alignment
                    synced_code = append_duration_sync(current_code, audio_duration)
                    
                    # Write current code to file
                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write(synced_code)
                    
                    # Try to compile
                    update_job_status(job_id, progress=scene_progress, current_step='code', message=f"→ Scene {index}/{len(video_data)} (Compiling)")
                    video_path, compile_error = compile_video(filepath, current_class_name, topic_slug, index)
                    
                    if video_path and os.path.exists(video_path):
                        # Success! Exit the REPL loop
                        print(f"[REPL] Scene {index} compiled successfully on iteration {repl_iteration + 1}")
                        break
                    
                    if compile_error and repl_iteration < max_repl_iterations - 1:
                        # Error occurred, try to fix with LLM
                        update_job_status(job_id, progress=scene_progress, current_step='code', 
                                         message=f'Fixing scene {index} (attempt {repl_iteration + 2}/{max_repl_iterations})...')
                        
                        fixed_code = fix_manim_code(
                            client=client,
                            original_code=current_code,
                            error_message=compile_error,
                            class_name=current_class_name,
                            provider=provider,
                            model=model
                        )
                        
                        if fixed_code:
                            current_code = fixed_code.get('content', '')
                            current_class_name = fixed_code.get('class_name', current_class_name)
                        else:
                            # LLM couldn't fix it, break out of REPL loop
                            print(f"[REPL] Could not fix scene {index}, skipping")
                            break
                    else:
                        # No error message or max iterations reached
                        break
                
                if video_path and os.path.exists(video_path):
                    # Save compiled scene to cache
                    try:
                        os.makedirs(scene_cache_dir, exist_ok=True)
                        shutil.copyfile(video_path, str(cache_mp4))
                        with open(str(cache_py), 'w', encoding='utf-8') as f:
                            f.write(current_code)
                        meta = {
                            "text": text,
                            "animation": animation,
                            "index": index,
                            "class_name": current_class_name,
                            "audio_duration": audio_duration,
                            "provider": provider,
                            "model": model
                        }
                        with open(str(cache_json), 'w', encoding='utf-8') as f:
                            json.dump(meta, f, indent=2)
                        print(f"  [SCENE CACHE STORE] Saved scene {index} to cache (key: {cache_key[:12]})")
                        update_job_status(job_id, progress=scene_progress, current_step='code', message=f"✓ Scene {index}/{len(video_data)} (Generated: {cache_key[:8]}...)")
                    except Exception as e:
                        print(f"  [WARNING] Failed to store scene {index} in cache: {e}")
                    
                    generated_videos.append(video_path)
                    previous_context = {
                        'text': text,
                        'animation': animation,
                        'code': current_code
                    }
        
        if not generated_videos:
            raise Exception("No videos were generated")
        
        # Step 5: Concatenate Videos
        update_job_status(job_id, progress=80, current_step='video', 
                         message='Concatenating video scenes...')
        
        silent_video_path = str(media_dir / f"output_silent_{job_id}.mp4")
        success = concatenate_videos(generated_videos, silent_video_path)
        
        if not success:
            raise Exception("Failed to concatenate videos")
        
        # Step 6: Merge Audio (if available)
        final_output_path = str(media_dir / f"output_{job_id}.mp4")
        
        if audio_path and os.path.exists(audio_path):
            update_job_status(job_id, progress=90, current_step='video', 
                             message='Merging audio with video...')
            
            merge_success = merge_video_and_audio(
                video_path=silent_video_path,
                audio_path=audio_path,
                output_path=final_output_path
            )
            
            if not merge_success:
                # If merge fails, use silent video
                final_output_path = silent_video_path
        else:
            # No audio, use silent video
            os.rename(silent_video_path, final_output_path)
        
        # Complete!
        video_url = f"/media/{os.path.basename(final_output_path)}"
        update_job_status(job_id, status='completed', progress=100, current_step='video', 
                         message='Video generation completed!', video_url=video_url)
        
        # Cleanup
        if os.path.exists(json_file):
            os.remove(json_file)
        
    except Exception as e:
        update_job_status(job_id, status='failed', error=str(e), 
                         message=f'Error: {str(e)}')


def start_video_generation(topic, enable_tts=True, llm_provider='auto', tts_provider=None, tts_voice=None, tts_rate=None, bypass_cache=False, bypass_scene_cache=False):
    """Start video generation in background thread"""
    
    job_id = str(uuid.uuid4())
    
    # Initialize job
    jobs[job_id] = {
        'job_id': job_id,
        'topic': topic,
        'status': 'queued',
        'progress': 0,
        'current_step': 'script',
        'message': 'Job queued',
        'enable_tts': enable_tts,
        'llm_provider': llm_provider,
        'tts_provider': tts_provider,
        'tts_voice': tts_voice,
        'tts_rate': tts_rate,
        'bypass_cache': bypass_cache,
        'bypass_scene_cache': bypass_scene_cache,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }
    
    thread = threading.Thread(
        target=generate_script_workflow,
        args=(job_id, topic, enable_tts, llm_provider, tts_provider, tts_voice, tts_rate, bypass_cache, bypass_scene_cache),
        daemon=True
    )
    thread.start()
    
    return job_id


def continue_video_generation(job_id, video_data):
    """Resumes the video generation pipeline with edited video_data"""
    if job_id not in jobs:
        return False
        
    job = jobs[job_id]
    if job['status'] != 'awaiting_review':
        return False
        
    job['video_data'] = video_data
    job['status'] = 'queued'
    job['progress'] = 25
    job['current_step'] = 'tts'
    job['message'] = 'Rendering pipeline started with custom script'
    job['updated_at'] = datetime.now().isoformat()
    
    thread = threading.Thread(
        target=generate_rendering_workflow,
        args=(job_id,),
        daemon=True
    )
    thread.start()
    return True


def get_job_status(job_id):
    """Get current status of a job"""
    return jobs.get(job_id)

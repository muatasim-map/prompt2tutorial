from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import sys
from pathlib import Path

# Add parent directory to path to enable imports
sys.path.insert(0, str(Path(__file__).parent))

from video_generator import start_video_generation, get_job_status

# Get absolute path to frontend directory
FRONTEND_DIR = Path(__file__).parent / 'frontend'

app = Flask(__name__, 
            static_folder=str(FRONTEND_DIR),
            static_url_path='/static')
CORS(app)

# Ensure media directory exists (at project root level)
PROJECT_ROOT = Path(__file__).parent.parent
MEDIA_DIR = PROJECT_ROOT / 'media'
os.makedirs(MEDIA_DIR, exist_ok=True)


@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/api/generate', methods=['POST'])
def generate_video():
    """Start video generation"""
    try:
        data = request.get_json()
        
        topic = data.get('topic')
        if not topic:
            return jsonify({'error': 'Topic is required'}), 400

        # Refuse to start a job on stale code. Auto-reload is disabled, so a
        # server started before an edit keeps running the OLD modules and fails
        # minutes later with a misleading, long-since-removed error message.
        from app_build import build_info
        info = build_info()
        if info['stale']:
            return jsonify({
                'error': (
                    'Server is running OUTDATED code (source changed since startup). '
                    'Stop this server and run "python src/main.py" again, then retry. '
                    f"running_build={info['build_id']}"
                ),
                'error_category': 'stale_server',
                'build': info,
            }), 503

        llm_provider = data.get('llm_provider', 'auto')
        enable_tts = data.get('enable_tts', True)
        tts_provider = data.get('tts_provider')
        tts_voice = data.get('tts_voice')
        tts_rate = data.get('tts_rate')
        bypass_cache = data.get('bypass_cache', False)
        bypass_scene_cache = data.get('bypass_scene_cache', False)
        target_duration = data.get('target_duration', 60)
        
        # Start video generation
        job_id = start_video_generation(topic, enable_tts, llm_provider, tts_provider, tts_voice, tts_rate, bypass_cache, bypass_scene_cache, target_duration)
        
        return jsonify({
            'job_id': job_id,
            'status': 'queued',
            'message': 'Video generation started'
        }), 202
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/generate/continue', methods=['POST'])
def continue_video():
    """Continue video generation with revised script"""
    try:
        data = request.get_json()
        job_id = data.get('job_id')
        video_data = data.get('video_data')
        
        if not job_id:
            return jsonify({'error': 'Job ID is required'}), 400
        if not video_data:
            return jsonify({'error': 'Video data is required'}), 400
            
        from video_generator import continue_video_generation as resume_gen
        
        success = resume_gen(job_id, video_data)
        if success:
            return jsonify({
                'job_id': job_id,
                'status': 'queued',
                'message': 'Video generation resumed'
            }), 200
        else:
            return jsonify({'error': 'Job not found or invalid status'}), 404
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500
@app.route('/api/progress/<job_id>', methods=['GET'])
def get_progress(job_id):
    """Get video generation progress"""
    job = get_job_status(job_id)

    if not job:
        return jsonify({'error': 'Job not found'}), 404

    # The activity feed is append-only server-side; a client passes the highest
    # seq it has already rendered so each poll returns only what is new. Without
    # ?since= the whole (bounded) backlog comes back, which is what a page
    # reload or a late-joining client needs.
    payload = dict(job)
    feed = payload.get('messages') or []
    since = request.args.get('since', type=int)
    if since is not None:
        feed = [m for m in feed if m.get('seq', 0) > since]
    payload['messages'] = feed
    payload['message_seq'] = job.get('message_seq', 0)

    return jsonify(payload)


@app.route('/media/<path:filename>')
def serve_media(filename):
    """Serve generated media files"""
    return send_from_directory(MEDIA_DIR, filename)


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint.

    Includes the build fingerprint of the code this process is actually running.
    ``stale: true`` means the source on disk changed after the server started and
    the server must be restarted to pick it up (auto-reload is intentionally off).
    """
    from app_build import build_info
    return jsonify({
        'status': 'healthy',
        'service': 'Prompt2Learn.ai API',
        'build': build_info(),
    })


if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    print("=" * 80)
    print("Prompt2Learn.ai Server")
    print("=" * 80)
    print("Starting Flask server...")
    print(f"Access the web interface at: http://localhost:{port}")
    print("=" * 80)
    from app_build import BUILD_ID
    print(f"Build: {BUILD_ID}   (also shown at /api/health)")
    print("\nNOTE: Auto-reload is disabled to prevent job state loss during video generation")
    print("      Restart the server manually if you make code changes.")
    print("      If /api/health reports \"stale\": true, this process is running OLD code.\n")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=True,
        use_reloader=False,  # Disable auto-reload to prevent losing job state
        threaded=True
    )

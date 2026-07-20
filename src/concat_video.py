import subprocess
import os
import re
import sys
from pathlib import Path

def sanitize_filename(filename):
    """Remove or replace problematic characters from filenames"""
    # Remove apostrophes, question marks, and other special characters
    # Replace with underscores or remove them
    sanitized = re.sub(r"['\"\?!:;,\(\)\[\]\{\}]", "", filename)
    # Replace multiple underscores with single underscore
    sanitized = re.sub(r"_+", "_", sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip("_")
    return sanitized

def compile_video(file_path, class_name, topic_slug, index):
    """Compiles the video using Manim
    
    Returns:
        tuple: (video_path, error_message) - video_path is None if failed, error_message is None if success
    """
    try:
        # Resolve path to manim executable in the current virtual environment
        py_bin_dir = Path(sys.executable).parent
        manim_executable = "manim"
        possible_paths = [
            py_bin_dir / "manim.exe",
            py_bin_dir / "manim",
        ]
        for path in possible_paths:
            if path.exists():
                manim_executable = str(path)
                break
                
        cmd = [manim_executable, "-ql", file_path, class_name]
        print(f"\nCompiling: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes timeout
        )
        
        if result.returncode == 0:
            print(f"[OK] Video compiled successfully")
            # Manim creates directory based on the Python filename (without extension)
            # Extract filename without extension from file_path
            filename_without_ext = os.path.splitext(os.path.basename(file_path))[0]
            # Video will be in media/videos/{filename_without_extension}/480p15/{class_name}.mp4
            video_path = f"media/videos/{filename_without_ext}/480p15/{class_name}.mp4"
            return video_path, None
        else:
            error_msg = result.stderr
            print(f"[ERROR] Error compiling video:")
            print(error_msg)
            return None, error_msg
            
    except subprocess.TimeoutExpired:
        error_msg = "Timeout: Compilation took more than 5 minutes"
        print(f"[ERROR] {error_msg}")
        return None, error_msg
    except Exception as e:
        error_msg = str(e)
        print(f"[ERROR] Error: {error_msg}")
        return None, error_msg


def concatenate_videos(video_paths, output_path):
    """Joins all videos into one using PyAV (av) to avoid external ffmpeg dependencies"""
    if not video_paths:
        print("[ERROR] No videos to concatenate")
        return False
    
    import av
    from pathlib import Path
    
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    list_file = "media/video_list.txt"
    
    # Write list file using the format expected by av's concat demuxer
    with open(list_file, 'w', encoding="utf-8") as f:
        f.write("# Concat list\n")
        for video_path in video_paths:
            if os.path.exists(video_path):
                abs_path = Path(video_path).resolve().as_posix()
                f.write(f"file 'file:{abs_path}'\n")
                
    try:
        print(f"\n  Concatenating {len(video_paths)} videos using PyAV...")
        input_container = av.open(list_file, options={"safe": "0"}, format="concat")
        input_stream = input_container.streams.video[0]
        
        output_container = av.open(output_path, mode="w")
        output_stream = output_container.add_stream(template=input_stream)
        
        for packet in input_container.demux(input_stream):
            if packet.dts is None:
                continue
            packet.dts = None
            packet.stream = output_stream
            output_container.mux(packet)
            
        input_container.close()
        output_container.close()
        
        if os.path.exists(list_file):
            os.remove(list_file)
            
        print(f"[OK] Final video created: {output_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Error concatenating videos via PyAV: {e}")
        if os.path.exists(list_file):
            os.remove(list_file)
        return False


def merge_video_and_audio(video_path, audio_path, output_path):
    """
    Merges video and audio files into a single MP4 file using PyAV (av)
    """
    if not os.path.exists(video_path):
        print(f"[ERROR] Video file not found: {video_path}")
        return False
    
    if not os.path.exists(audio_path):
        print(f"[ERROR] Audio file not found: {audio_path}")
        return False
    
    import av
    
    try:
        print(f"\n{'='*80}")
        print(f"MERGING VIDEO AND AUDIO USING PyAV")
        print(f"{'='*80}")
        print(f"Video: {video_path}")
        print(f"Audio: {audio_path}")
        print(f"Output: {output_path}\n")
        
        with av.open(video_path) as video_input, av.open(audio_path) as audio_input:
            video_stream = video_input.streams.video[0]
            audio_stream = audio_input.streams.audio[0]
            
            output_container = av.open(output_path, mode="w")
            output_video_stream = output_container.add_stream(template=video_stream)
            output_audio_stream = output_container.add_stream(template=audio_stream)
            
            for packet in video_input.demux(video_stream):
                if packet.dts is None:
                    continue
                packet.stream = output_video_stream
                output_container.mux(packet)
                
            for packet in audio_input.demux(audio_stream):
                if packet.dts is None:
                    continue
                packet.stream = output_audio_stream
                output_container.mux(packet)
                
            output_container.close()
            
        print(f"[OK] Final video with audio created: {output_path}\n")
        return True
    except Exception as e:
        print(f"[ERROR] Error merging video and audio via PyAV: {e}")
        return False

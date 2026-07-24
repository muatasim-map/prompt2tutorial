import sys
import asyncio
import json
from pathlib import Path
from typing import Optional
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MathToManimClient:
    """Headless Client wrapper for querying the local Math-To-Manim MCP server."""
    
    def __init__(self, mcp_repo_path: Optional[str] = None):
        # Fallback to Desktop path if not provided
        self.repo_path = mcp_repo_path or r"C:\Users\HP\Desktop\math-to-manim"
        
    async def generate_math_animation_code(self, prompt: str, quality: str = "l", offline: bool = False) -> Optional[str]:
        """
        Sends the prompt to Math-To-Manim MCP server, polls until completed,
        and returns the generated python code.
        """
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mythos.cli", "serve-mcp"],
            cwd=str(self.repo_path),
        )
        
        try:
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    
                    # 1. Start the animation generation job (set render=False as we compile locally)
                    params = {
                        "prompt": prompt,
                        "render": False,
                        "quality": quality,
                        "offline": offline
                    }
                    
                    created = await session.call_tool("m2m_create_animation", {"params": params})
                    content_blocks = getattr(created, "content", [])
                    text_content = "".join(block.text for block in content_blocks if getattr(block, "text", None))
                    
                    job = json.loads(text_content)
                    job_id = job.get("id")
                    if not job_id:
                        print(f"[MCP ERROR] Failed to create job. Raw response: {text_content}")
                        return None
                        
                    print(f"[MCP CLIENT] Job created. ID: {job_id}. Status: {job.get('status')}. Polling...")
                    
                    # 2. Poll the job status
                    max_polls = 100
                    poll_interval = 10 # seconds
                    for poll_idx in range(max_polls):
                        await asyncio.sleep(poll_interval)
                        polled = await session.call_tool("m2m_get_job", {"job_id": job_id})
                        
                        poll_content = getattr(polled, "content", [])
                        poll_text = "".join(block.text for block in poll_content if getattr(block, "text", None))
                        
                        state = json.loads(poll_text)
                        status = state.get("status")
                        
                        if status == "completed":
                            run_id = state.get("run_id")
                            print(f"[MCP CLIENT] Job completed! Run ID: {run_id}. Fetching scene code...")
                            
                            # 3. Retrieve the generated scene code
                            code_result = await session.call_tool("m2m_get_scene_code", {"params": {"run_id": run_id}})
                            code_blocks = getattr(code_result, "content", [])
                            code_text = "".join(block.text for block in code_blocks if getattr(block, "text", None))
                            return code_text
                            
                        elif status == "failed":
                            error_msg = state.get("error", "Unknown error")
                            print(f"[MCP CLIENT] Job failed on server. Error: {error_msg}")
                            return None
                            
                        else:
                            # Still running, log progress if available
                            manifest = state.get("manifest") or {}
                            stages = manifest.get("stages", [])
                            latest_stage = stages[-1]["stage"] if stages else "queued"
                            print(f"[MCP CLIENT] Poll {poll_idx+1}/{max_polls}: status={status}, stage={latest_stage}")
                            
                    print("[MCP CLIENT] Polling timed out.")
                    return None
                    
        except Exception as e:
            print(f"[MCP CLIENT] Connection / execution failed: {e}")
            return None

# Simple command-line test harness
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python mcp_client.py 'explain vector dot product'")
        sys.exit(1)
        
    test_prompt = sys.argv[1]
    client = MathToManimClient()
    print(f"Running test for prompt: '{test_prompt}'")
    code = asyncio.run(client.generate_math_animation_code(test_prompt, offline=True))
    if code:
        print("\n--- SUCCESSFULLY RETRIEVED MANIM CODE ---")
        print(code[:1000] + "\n...")
        print("------------------------------------------")
    else:
        print("\n[TEST] Failed to retrieve code.")

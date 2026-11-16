import os,sys,ctypes
if sys.platform.startswith("linux"):
    ctypes.CDLL(os.path.join(os.path.dirname(__file__),"..","vendor","libexpat.so.1"),mode=ctypes.RTLD_GLOBAL)
os.environ.setdefault('HF_HOME','/tmp/huggingface')
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..','backend')))
from app import app,cover_scene_analysis
app.add_api_route('/api/index.py',cover_scene_analysis,methods=['POST'])

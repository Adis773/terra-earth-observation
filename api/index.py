import os,sys
os.environ.setdefault('HF_HOME','/tmp/huggingface')
os.environ.setdefault('MPLCONFIGDIR','/tmp/matplotlib')
sys.path.insert(0,os.path.abspath(os.path.join(os.path.dirname(__file__),'..','backend')))
from app import app

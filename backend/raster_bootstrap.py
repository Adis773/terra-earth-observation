"""Keep GDAL's trusted CA bundle accessible on Windows paths with non-ASCII names."""
import os
if os.name == 'nt':
    try:
        import certifi, ctypes
        original=certifi.where()
        original.encode('ascii')
    except UnicodeEncodeError:
        buffer=ctypes.create_unicode_buffer(32768)
        length=ctypes.windll.kernel32.GetShortPathNameW(original,buffer,len(buffer))
        if length and length < len(buffer):
            short=buffer.value
            try: short.encode('ascii')
            except UnicodeEncodeError: pass
            else: certifi.where=lambda:short

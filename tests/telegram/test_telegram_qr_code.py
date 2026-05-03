#!/usr/bin/env python3
"""
Test script to fetch Telegram QR code and test saving/loading

Live API: skipped in pytest unless DECISIONSAI_NETWORK_TESTS=1.
"""
import requests
import base64
import tempfile
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DECISIONSAI_NETWORK_TESTS", "").strip().lower()
    not in ("1", "true", "yes", "on"),
    reason="Live DecisionsAI API; set DECISIONSAI_NETWORK_TESTS=1 to run",
)

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_qr_code_fetch():
    """Test fetching QR code from server"""
    print("=" * 60)
    print("Testing Telegram QR Code Fetch and Save")
    print("=" * 60)
    
    # Server URL
    server_base_url = "https://www.decisionsai.net"
    api_url = f"{server_base_url.rstrip('/')}/api/telegram/link/request/"
    
    print(f"\n1. Making request to: {api_url}")
    
    # Try with and without API key
    headers = {'Content-Type': 'application/json'}
    payload = {}
    
    # Check if API key is in environment
    api_key = os.environ.get('MAIN_PRODUCT_API_KEY', '').strip()
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
        print(f"   Using API key (length: {len(api_key)})")
    else:
        print("   No API key found in environment")
    
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        print(f"\n2. Response status: {response.status_code}")
        print(f"   Response headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\n3. Response data keys: {list(data.keys())}")
            
            # Check for QR code
            qr_code_data = data.get('qr_code')
            connection_token = data.get('token')
            connection_link = data.get('link')
            app_user_id = data.get('app_user_id')
            
            print(f"\n4. Connection token: {connection_token[:30] if connection_token else None}...")
            print(f"   Connection link: {connection_link}")
            print(f"   App user ID: {app_user_id}")
            print(f"   Has QR code: {bool(qr_code_data)}")
            
            if qr_code_data:
                print(f"\n5. QR code data type: {type(qr_code_data)}")
                print(f"   QR code data length: {len(qr_code_data)}")
                print(f"   QR code starts with: {qr_code_data[:50]}...")
                
                # Check if it's a data URI
                if qr_code_data.startswith('data:image'):
                    print(f"   ✓ QR code is in data URI format")
                    if ',' in qr_code_data:
                        # Extract base64 part
                        qr_code_data = qr_code_data.split(',', 1)[1]
                        print(f"   ✓ Extracted base64 data (length: {len(qr_code_data)})")
                    else:
                        print(f"   ✗ ERROR: Data URI format invalid - no comma found")
                        return
                else:
                    print(f"   ✓ QR code is raw base64")
                
                # Decode base64
                try:
                    qr_bytes = base64.b64decode(qr_code_data)
                    print(f"\n6. Decoded QR code bytes: {len(qr_bytes)} bytes")
                    print(f"   First 20 bytes (hex): {qr_bytes[:20].hex()}")
                    
                    # Check if it's a valid PNG (should start with PNG signature)
                    png_signature = b'\x89PNG\r\n\x1a\n'
                    if qr_bytes.startswith(png_signature):
                        print(f"   ✓ Valid PNG signature detected")
                    else:
                        print(f"   ✗ WARNING: Doesn't look like a PNG file")
                        print(f"   First bytes: {qr_bytes[:10]}")
                    
                    # Save to temp file
                    print(f"\n7. Saving to temp file...")
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
                        tmp_file.write(qr_bytes)
                        tmp_path = tmp_file.name
                    
                    print(f"   ✓ Saved to: {tmp_path}")
                    print(f"   File size: {os.path.getsize(tmp_path)} bytes")
                    print(f"   File exists: {os.path.exists(tmp_path)}")
                    
                    # Try to load with PyQt6
                    print(f"\n8. Testing PyQt6 QPixmap loading...")
                    try:
                        from PyQt6.QtGui import QPixmap
                        from PyQt6.QtWidgets import QApplication
                        
                        # Create QApplication if it doesn't exist
                        app = QApplication.instance()
                        if app is None:
                            app = QApplication([])
                        
                        # Try loading from file
                        pixmap = QPixmap(tmp_path)
                        if not pixmap.isNull():
                            print(f"   ✓ Successfully loaded QPixmap from file")
                            print(f"   Pixmap size: {pixmap.width()}x{pixmap.height()}")
                            print(f"   Pixmap is null: {pixmap.isNull()}")
                        else:
                            print(f"   ✗ ERROR: QPixmap is null after loading from file")
                        
                        # Try loading from data
                        print(f"\n9. Testing direct load from data...")
                        pixmap2 = QPixmap()
                        if pixmap2.loadFromData(qr_bytes, "PNG"):
                            print(f"   ✓ Successfully loaded QPixmap from data")
                            print(f"   Pixmap size: {pixmap2.width()}x{pixmap2.height()}")
                        else:
                            print(f"   ✗ ERROR: Failed to load QPixmap from data")
                        
                        # Clean up
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                            print(f"\n10. ✓ Cleaned up temp file")
                        
                    except ImportError:
                        print(f"   ✗ ERROR: PyQt6 not available for testing")
                    except Exception as e:
                        print(f"   ✗ ERROR loading with PyQt6: {e}")
                        import traceback
                        traceback.print_exc()
                        # Clean up
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                except Exception as e:
                    print(f"\n6. ✗ ERROR decoding base64: {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"\n5. ✗ QR code is missing from server response")
                print(f"   The server should return 'qr_code' field but it's not present")
                print(f"   This is a server-side issue - the server needs to generate and return the QR code")
                print(f"\n   Response data: {data}")
        else:
            print(f"\n3. ✗ Request failed")
            print(f"   Response text: {response.text[:500]}")
            try:
                error_data = response.json()
                print(f"   Error data: {error_data}")
            except:
                pass
                
    except requests.exceptions.RequestException as e:
        print(f"\n✗ Request error: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_qr_code_fetch()


#!/usr/bin/env python3
"""
Test script to investigate Telegram token flow and see what the server returns

Live API: skipped in pytest unless DECISIONSAI_NETWORK_TESTS=1.
"""
import requests
import json
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

def test_token_flow():
    """Test the complete Telegram token flow"""
    print("=" * 70)
    print("Testing Telegram Token Flow")
    print("=" * 70)
    
    server_base_url = "https://www.decisionsai.net"
    
    # Step 1: Request connection link
    print("\n" + "=" * 70)
    print("STEP 1: Request Connection Link")
    print("=" * 70)
    
    api_url = f"{server_base_url.rstrip('/')}/api/telegram/link/request/"
    headers = {'Content-Type': 'application/json'}
    payload = {}
    
    # Check for API key
    api_key = os.environ.get('MAIN_PRODUCT_API_KEY', '').strip()
    if api_key:
        headers['Authorization'] = f'Bearer {api_key}'
        print(f"Using API key (length: {len(api_key)})")
    else:
        print("No API key found in environment")
    
    try:
        print(f"\nRequesting: POST {api_url}")
        print(f"Headers: {json.dumps({k: v[:20] + '...' if k == 'Authorization' and len(v) > 20 else v for k, v in headers.items()}, indent=2)}")
        print(f"Payload: {json.dumps(payload, indent=2)}")
        
        response = requests.post(api_url, headers=headers, json=payload, timeout=10)
        
        print(f"\nResponse Status: {response.status_code}")
        print(f"Response Headers: {dict(response.headers)}")
        
        if response.status_code == 200:
            data = response.json()
            print(f"\nResponse Data:")
            print(json.dumps(data, indent=2))
            
            token = data.get('token')
            link = data.get('link')
            short_code = data.get('short_code')
            app_user_id = data.get('app_user_id')
            qr_code = data.get('qr_code')
            
            print(f"\n" + "-" * 70)
            print("EXTRACTED VALUES:")
            print("-" * 70)
            print(f"Token: {token}")
            print(f"Token length: {len(token) if token else 0}")
            print(f"\nLink: {link}")
            print(f"Link contains token: {'token' in link.lower() if link else False}")
            
            # Extract token from link if present
            if link and '?start=' in link:
                link_token = link.split('?start=')[1] if '?start=' in link else None
                print(f"Token from link: {link_token}")
                print(f"Tokens match: {token == link_token if token and link_token else False}")
            
            print(f"\nShort Code: {short_code}")
            print(f"App User ID: {app_user_id}")
            print(f"Has QR Code: {bool(qr_code)}")
            if qr_code:
                print(f"QR Code length: {len(qr_code)}")
                print(f"QR Code starts with: {qr_code[:50]}...")
            
            # Step 2: Check status endpoint
            print("\n" + "=" * 70)
            print("STEP 2: Check Status Endpoint")
            print("=" * 70)
            
            if token:
                status_url = f"{server_base_url.rstrip('/')}/api/telegram/link/status/"
                status_params = {'token': token}
                
                print(f"\nRequesting: GET {status_url}")
                print(f"Params: {json.dumps(status_params, indent=2)}")
                
                status_response = requests.get(
                    status_url,
                    params=status_params,
                    headers=headers,
                    timeout=5
                )
                
                print(f"\nStatus Response: {status_response.status_code}")
                if status_response.status_code == 200:
                    status_data = status_response.json()
                    print(f"Status Data:")
                    print(json.dumps(status_data, indent=2))
                else:
                    print(f"Status Response Text: {status_response.text[:500]}")
            
            # Step 3: Analyze the link format
            print("\n" + "=" * 70)
            print("STEP 3: Link Format Analysis")
            print("=" * 70)
            
            if link:
                print(f"\nFull Link: {link}")
                print(f"\nLink Breakdown:")
                if link.startswith('https://t.me/'):
                    parts = link.replace('https://t.me/', '').split('?')
                    bot_username = parts[0] if parts else None
                    print(f"  Bot Username: {bot_username}")
                    
                    if len(parts) > 1:
                        params = parts[1]
                        print(f"  Parameters: {params}")
                        if 'start=' in params:
                            start_value = params.split('start=')[1].split('&')[0]
                            print(f"  Start Parameter Value: {start_value}")
                            print(f"  Start Value Length: {len(start_value)}")
                            print(f"  Matches Token: {start_value == token if token else False}")
                else:
                    print(f"  WARNING: Link doesn't start with https://t.me/")
            
            # Step 4: Test what Telegram would receive
            print("\n" + "=" * 70)
            print("STEP 4: What Telegram Bot Would Receive")
            print("=" * 70)
            
            if link and '?start=' in link:
                telegram_token = link.split('?start=')[1].split('&')[0] if '?start=' in link else None
                print(f"\nWhen user clicks link or scans QR code:")
                print(f"  Telegram opens: t.me/{link.split('t.me/')[1].split('?')[0] if 't.me/' in link else 'unknown'}")
                print(f"  Telegram sends to bot: /start {telegram_token}")
                print(f"  Token that would be sent: {telegram_token}")
                print(f"  Token length: {len(telegram_token) if telegram_token else 0}")
                print(f"  Matches server token: {telegram_token == token if telegram_token and token else False}")
            else:
                print(f"\nWARNING: Link doesn't contain ?start= parameter!")
                print(f"This means Telegram won't send the token to the bot!")
        
        else:
            print(f"\nRequest failed with status {response.status_code}")
            print(f"Response text: {response.text[:500]}")
            try:
                error_data = response.json()
                print(f"Error data: {json.dumps(error_data, indent=2)}")
            except:
                pass
                
    except requests.exceptions.RequestException as e:
        print(f"\nRequest error: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_token_flow()



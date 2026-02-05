"""
Test script to diagnose LINE API connection issues
"""
import socket
import time
import requests
from urllib3.util.timeout import Timeout

def test_dns_resolution():
    """Test if api.line.me can be resolved"""
    print("Testing DNS resolution for api.line.me...")
    try:
        ip = socket.gethostbyname("api.line.me")
        print(f"[OK] DNS resolution successful: api.line.me -> {ip}")
        return True
    except socket.gaierror as e:
        print(f"[FAIL] DNS resolution failed: {e}")
        return False

def test_tcp_connection():
    """Test if we can establish TCP connection to api.line.me:443"""
    print("\nTesting TCP connection to api.line.me:443...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        start_time = time.time()
        result = sock.connect_ex(("api.line.me", 443))
        elapsed = time.time() - start_time
        sock.close()
        
        if result == 0:
            print(f"[OK] TCP connection successful (took {elapsed:.2f}s)")
            return True
        else:
            print(f"[FAIL] TCP connection failed with error code: {result}")
            return False
    except Exception as e:
        print(f"[FAIL] TCP connection error: {e}")
        return False

def test_https_request():
    """Test if we can make HTTPS request to LINE API"""
    print("\nTesting HTTPS request to api.line.me...")
    try:
        start_time = time.time()
        # Use a simple endpoint that doesn't require authentication
        response = requests.get("https://api.line.me", timeout=30)
        elapsed = time.time() - start_time
        print(f"[OK] HTTPS request successful (took {elapsed:.2f}s)")
        print(f"   Status code: {response.status_code}")
        return True
    except requests.exceptions.Timeout as e:
        print(f"[FAIL] HTTPS request timed out: {e}")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"[FAIL] Connection error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] HTTPS request error: {e}")
        return False

def main():
    print("=" * 60)
    print("LINE API Connection Diagnostic Tool")
    print("=" * 60)
    
    results = {
        "DNS": test_dns_resolution(),
        "TCP": test_tcp_connection(),
        "HTTPS": test_https_request()
    }
    
    print("\n" + "=" * 60)
    print("Summary:")
    print("=" * 60)
    for test_name, result in results.items():
        status = "[OK] PASS" if result else "[FAIL] FAIL"
        print(f"{test_name}: {status}")
    
    print("\n" + "=" * 60)
    if all(results.values()):
        print("[OK] All tests passed! Network connection to LINE API is working.")
        print("\nIf you're still experiencing issues, the problem might be:")
        print("  - Temporary network congestion")
        print("  - Firewall blocking outbound HTTPS connections")
        print("  - Antivirus software interfering with connections")
    else:
        print("[FAIL] Some tests failed. Possible issues:")
        if not results["DNS"]:
            print("  - DNS server not responding or blocking api.line.me")
            print("  - Check your DNS settings (try 8.8.8.8 or 1.1.1.1)")
        if not results["TCP"]:
            print("  - Firewall blocking port 443")
            print("  - Network routing issues")
        if not results["HTTPS"]:
            print("  - SSL/TLS certificate issues")
            print("  - Proxy server blocking HTTPS")
    print("=" * 60)

if __name__ == "__main__":
    main()

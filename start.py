import AppKit

AppKit.NSBundle.mainBundle().infoDictionary()['LSUIElement'] = '1'
from distr.app import run

# Main execution block
if __name__ == "__main__":
    print("Starting Decisions...")
    print("Please note: that this is an early version and may not work as expected.")
    print("It may take a while to start up initially, as it needs to download model weights.")
    run()

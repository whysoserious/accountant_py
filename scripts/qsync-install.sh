#!/bin/bash
# QSync Client installer for Fedora 43
# Adapted from https://gist.github.com/kamkarthi/7d71bf951d44c87321ca227e66796810
#
# Usage:
#   sudo ./qsync-install.sh          # install
#   sudo ./qsync-install.sh uninstall # uninstall

set -euo pipefail

QSYNC_URL="https://download.qnap.com/Storage/Utility/QNAPQsyncClientUbuntux64-1.0.8.0623.deb"
INSTALL_DIR="/usr/local/bin/QNAP/QsyncClient"
LIB_DIR="/usr/local/lib/QNAP/QsyncClient"
WORK_DIR=$(mktemp -d)

# --- Helpers ---

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "Error: run with sudo"
        exit 1
    fi
}

# --- Uninstall ---

do_uninstall() {
    check_root
    echo "Uninstalling QSync Client..."

    rm -rf /usr/local/bin/QNAP/QsyncClient
    rm -rf /usr/local/lib/QNAP/QsyncClient
    rm -f /usr/share/pixmaps/Qsync.png
    rm -f /usr/share/applications/QNAPQsyncClient.desktop

    # Clean up empty parent dirs
    rmdir /usr/local/bin/QNAP 2>/dev/null || true
    rmdir /usr/local/lib/QNAP 2>/dev/null || true

    echo "Done. Remove user files manually if needed:"
    echo "  rm -rf ~/.config/QNAP ~/.local/share/QNAP"
    echo "  rm -f ~/.config/autostart/QNAPQsyncClient.desktop"
    echo "  rm -f ~/.local/share/applications/QNAPQsyncClient.desktop"
}

# --- Install ---

do_install() {
    check_root

    if [ -d "$INSTALL_DIR" ]; then
        echo "QSync already installed at $INSTALL_DIR"
        echo "Run '$0 uninstall' first to reinstall."
        exit 1
    fi

    # Check dependencies
    echo "Checking dependencies..."
    local missing=()
    for pkg in qt5-qtbase qt5-qtbase-gui libusb1; do
        if ! rpm -q "$pkg" &>/dev/null; then
            missing+=("$pkg")
        fi
    done
    if [ ${#missing[@]} -gt 0 ]; then
        echo "Installing missing packages: ${missing[*]}"
        dnf install -y "${missing[@]}"
    fi

    # Download and extract
    echo "Downloading QSync Client..."
    cd "$WORK_DIR"
    curl -fsSL -o qsync.deb "$QSYNC_URL"

    echo "Extracting..."
    ar x qsync.deb
    if [ -f data.tar.xz ]; then
        xz -d data.tar.xz
    elif [ -f data.tar.zst ]; then
        zstd -d data.tar.zst
    fi
    tar xf data.tar

    # Install binaries
    echo "Installing to $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"
    cp -a usr/local/bin/QNAP/QsyncClient/* "$INSTALL_DIR/"

    # Install bundled libraries
    echo "Installing libraries to $LIB_DIR..."
    mkdir -p "$LIB_DIR"
    cp -a usr/local/lib/QNAP/QsyncClient/* "$LIB_DIR/"

    # Create the missing symlink that the gist identified
    if [ ! -e "$LIB_DIR/libQUiLib.so.1" ]; then
        ln -s libQUiLib.so.1.0.0 "$LIB_DIR/libQUiLib.so.1"
    fi

    # Install icon and desktop file
    if [ -f usr/share/pixmaps/Qsync.png ]; then
        cp usr/share/pixmaps/Qsync.png /usr/share/pixmaps/
    fi
    if [ -f usr/share/applications/QNAPQsyncClient.desktop ]; then
        cp usr/share/applications/QNAPQsyncClient.desktop /usr/share/applications/
    fi

    # Cleanup
    rm -rf "$WORK_DIR"

    echo ""
    echo "QSync Client installed successfully."
    echo ""
    echo "To set up for your user (run WITHOUT sudo):"
    echo "  mkdir -p ~/.config/autostart"
    echo "  cp /usr/share/applications/QNAPQsyncClient.desktop ~/.config/autostart/"
    echo ""
    echo "To launch:"
    echo "  /usr/local/bin/QNAP/QsyncClient/Qsync.sh"
}

# --- Main ---

case "${1:-install}" in
    uninstall)
        do_uninstall
        ;;
    install)
        do_install
        ;;
    *)
        echo "Usage: $0 [install|uninstall]"
        exit 1
        ;;
esac

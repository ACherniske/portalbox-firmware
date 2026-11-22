# Building Micropython with WPA2 Enterprise Support for ESP32S3

Complete step-by-step guide to build Micropython firmware with WPA2 Enterprise Wifi.

## Hardware
- ESP32-S3 development board with quad PSRAM (`R2`, e.g. N8R2) or no PSRAM. Not octal PSRAM (`R8`): it uses GPIO35-37, which the card reader needs (see README).
- USB cable for flashing

## Step 1: Install ESP-IDF v5.5.1

ESP-IDF (Espressif IoT Development Framework) is required to build MicroPython for ESP32.

```bash
# Create a directory for ESP-IDF
mkdir -p ~/esp
cd ~/esp

# Clone ESP-IDF v5.5.1
git clone -b v5.5.1 --recursive https://github.com/espressif/esp-idf.git

# Navigate into ESP-IDF directory
cd esp-idf (or similar name directory)

# Install ESP-IDF tools (this will take a while)
./install.sh

# Verify installation
ls install.sh export.sh
```

## Step 2: Export ESP-IDF Environment Variables

Every time you open a new terminal to build MicroPython, you need to source the ESP-IDF environment:

```bash
cd ~/esp/esp-idf
source export.sh
```

You should see output like:
```
Done! You can now compile ESP-IDF projects.
Go to the project directory and run:
  idf.py build
```

## Step 3: Clone MicroPython with WPA2 Enterprise Support

There are a few ways to do this. One is to clone this repo with recursion so that the submodule is also retrieved:

```bash
git clone --recursive <repository-url>
```

Alternatively, you can clone MicroPython yourself and navigate to the branch with WPA2 Enterprise:

```bash
# Clone H-Milz fork of micropython
git clone https://github.com/h-milz/micropython.git
cd micropython

# Checkout the WPA2 enterprise branch
git checkout ports/esp32/add-wpa2-enterprise

# Verify you're on the correct branch
git branch
# Should show: * ports/esp32/add-wpa2-enterprise

# Verify WPA2 enterprise code is present
grep -n "ARG_eap_method" ports/esp32/network_wlan.c
# Should show line numbers with ARG_eap_method
```

## Step 4: Build mpy-cross

mpy-cross is the MicroPython cross-compiler. It compiles Python scripts to bytecode.

```bash
# From the micropython root directory
cd ~/micropython

# Build mpy-cross
make -C mpy-cross

# Verify it built successfully
ls mpy-cross/build/mpy-cross
# Should show: mpy-cross/build/mpy-cross

# Test it works
./mpy-cross/build/mpy-cross --version
```

The box loads precompiled modules made by this mpy-cross. Its `--version` must
say `mpy v6.3`, the version the firmware loads. `scripts/build.py` and
`scripts/deploy.py` run it from the `MPY_CROSS` variable (see "Load a box by
cable" in the README), e.g. from Windows through WSL:

```bash
export MPY_CROSS="wsl -e $HOME/micropython/mpy-cross/build/mpy-cross"
```

## Step 5: Configure ESP-IDF
```bash
# Enter the esp32 port folder
cd ports/esp32

# Set target board for esp-idf
idf.py set-target esp32s3

# Enable TinyUSB Stack
idf.py menuconfig
```

Navigate through the menu:
- Component config → TinyUSB Stack → Communication Device Class (CDC) → Enable TinyUSB CDC Feature

## Step 6: Get ESP32 Port Submodules

The ESP32 port requires additional submodules from ESP-IDF and other dependencies.

```bash
# Get all required submodules
make submodules

# This will take a few minutes as it downloads:
# - ESP-IDF components
# - Additional libraries
# - Board definitions

# Verify submodules were downloaded
ls -la
# You should see various subdirectories populated
```

## Step 7: Build MicroPython Firmware for ESP32-S3

```bash
# Build for ESP32-S3 (GENERIC_S3 board)
make BOARD=ESP32_GENERIC_S3

# Build process will take 5-15 minutes depending on your system
```

## Step 8: Flash Board

Make sure your board is connected before running the next command.

```bash
idf.py flash
```

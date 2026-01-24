#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import shutil
import time
import getpass
import socket

SOURCE_ROOTFS = os.path.abspath("./rootfs") 
MOUNT_POINT = "/mnt/toca_install"
MAPPER_NAME = "cryptroot"

class Style:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    GREEN = '\033[92m'
    WARN = '\033[93m'
    FAIL = '\033[91m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

class System:
    @staticmethod
    def run(cmd, shell=False, input_text=None, check=True):
        try:
            result = subprocess.run(
                cmd, 
                shell=shell, 
                check=check, 
                text=True, 
                input=input_text,
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"{Style.FAIL}Command failed: {' '.join(cmd) if isinstance(cmd, list) else cmd}{Style.RESET}", file=sys.stderr)
            print(f"{Style.FAIL}Stderr: {e.stderr.strip()}{Style.RESET}", file=sys.stderr)
            return None

    @staticmethod
    def list_disks():
        output = System.run(["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE", "-J"])
        if output:
            data = json.loads(output)
            return [d for d in data['blockdevices'] if d['type'] == 'disk']
        return []

    @staticmethod
    def write_file(path, content, append=False):
        mode = "a" if append else "w"
        with open(path, mode) as f:
            f.write(content)

    @staticmethod
    def get_uuid(device):
        return System.run(f"blkid -s UUID -o value {device}", shell=True)

class TocaInstaller:
    def __init__(self):
        self.disk = ""
        self.efi_part = ""
        self.root_part = ""
        self.luks_password = ""
        self.root_device_final = "" 
        self.use_luks = False
        
        self.selected_locale = "en_US.UTF-8"
        self.keymap = "us"
        
        self.username = "tocauser"
        self.password = "toca"
        self.hostname = "toca-machine"

    def header(self):
        os.system('clear')
        print(f"{Style.HEADER}{Style.BOLD}")
        print("  ┌──────────────────────────────────────────────────┐")
        print("  │            TOCA LINUX INSTALLER                  │")
        print("  └──────────────────────────────────────────────────┘")
        print(f"{Style.RESET}")

    def check_environment(self):
        if os.geteuid() != 0:
            print(f"{Style.FAIL}Run as root{Style.RESET}")
            sys.exit(1)
        
        if not os.path.exists(SOURCE_ROOTFS):
            print(f"{Style.FAIL}rootfs not found at {SOURCE_ROOTFS}{Style.RESET}")
            sys.exit(1)

        required_tools = ["mkfs.btrfs", "rsync", "parted", "nmcli", "wget", "cryptsetup"]
        for tool in required_tools:
            if shutil.which(tool) is None:
                print(f"{Style.FAIL}Missing tool: {tool}{Style.RESET}")
                sys.exit(1)

    def setup_network(self):
        while True:
            os.system('clear')
            print(f"{Style.BLUE}=== Network Configuration ==={Style.RESET}")
            print(f"Hostname: {Style.GREEN}{self.hostname}{Style.RESET}")
            
            ip_info = System.run("ip -br a | grep UP | awk '{print $1 \" \" $3}'", shell=True)
            print(f"Active Interfaces:\n{ip_info if ip_info else 'None'}")
            
            print("\n1. Set Hostname")
            print("2. Select Network Interface (Wi-Fi/Ethernet)")
            print("3. Test Connectivity (Ping)")
            print("4. Continue to Disk Selection")
            
            choice = input(f"\n{Style.WARN}Enter choice [1-4]: {Style.RESET}")
            
            if choice == '1':
                new_host = input("Enter new hostname: ").strip()
                if new_host: self.hostname = new_host
            
            elif choice == '2':
                self.configure_interface()
            
            elif choice == '3':
                print("Pinging google.com...")
                if os.system("ping -c 3 google.com > /dev/null 2>&1") == 0:
                    print(f"{Style.GREEN}Connected to internet.{Style.RESET}")
                else:
                    print(f"{Style.FAIL}No internet connection.{Style.RESET}")
                input("Press Enter...")
                
            elif choice == '4':
                break

    def configure_interface(self):
        devs_raw = System.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"])
        if not devs_raw: return
        
        devs = [line.split(':') for line in devs_raw.split('\n') if line]
        
        print(f"\n{Style.BLUE}Available Interfaces:{Style.RESET}")
        for i, d in enumerate(devs):
            print(f" [{i}] {d[0]} ({d[1]}) - {d[2]}")
            
        try:
            sel = int(input("Select interface: "))
            device, dev_type = devs[sel][0], devs[sel][1]
        except: return

        if dev_type == "wifi":
            self.configure_wifi(device)
        else:
            print(f"Attempting auto-connect on {device}...")
            System.run(["nmcli", "dev", "connect", device])

    def configure_wifi(self, device):
        print(f"Scanning on {device}...")
        System.run(["nmcli", "dev", "wifi", "rescan"])
        time.sleep(2)
        
        out = System.run(["nmcli", "-t", "-f", "SSID,SECURITY,BARS", "dev", "wifi", "list"])
        if not out:
            print("No networks found.")
            return

        networks = []
        seen = set()
        for line in out.split('\n'):
            parts = line.split(':')
            if len(parts) >= 2 and parts[0] and parts[0] not in seen:
                networks.append(line)
                seen.add(parts[0])

        for i, net in enumerate(networks):
            print(f" [{i}] {net.replace(':', ' | ')}")

        try:
            sel = int(input("Select Network: "))
            ssid = networks[sel].split(':')[0]
            pwd = getpass.getpass(f"Password for {ssid}: ")
            System.run(["nmcli", "dev", "wifi", "connect", ssid, "password", pwd])
        except: pass

    def select_locale_and_keymap(self):
        os.system('clear')
        print(f"{Style.BLUE}=== Localization Settings ==={Style.RESET}")

        common_locales = {
            "1": "en_US.UTF-8",
            "2": "pt_BR.UTF-8",
            "3": "en_GB.UTF-8",
            "4": "de_DE.UTF-8",
            "5": "es_ES.UTF-8",
            "6": "fr_FR.UTF-8",
        }
        print("Select your language (locale):")
        for k, v in common_locales.items():
            print(f" [{k}] {v}")
        
        choice = input("Enter choice or press Enter for 'en_US.UTF-8': ").strip()
        if choice in common_locales:
            self.selected_locale = common_locales[choice]
        
        print(f"Locale set to: {Style.GREEN}{self.selected_locale}{Style.RESET}\n")

        common_keymaps = {
            "1": ("us", "USA"),
            "2": ("br", "Brazil (abnt2)"),
            "3": ("gb", "Great Britain"),
            "4": ("de", "Germany"),
            "5": ("es", "Spain"),
            "6": ("fr", "France"),
        }
        print("Select your keyboard layout:")
        for k, v in common_keymaps.items():
            print(f" [{k}] {v[0]} ({v[1]})")
        
        choice = input("Enter choice or press Enter for 'us': ").strip()
        if choice in common_keymaps:
            self.keymap = common_keymaps[choice][0]

        print(f"Keyboard layout set to: {Style.GREEN}{self.keymap}{Style.RESET}")
        input("\nPress Enter to continue...")

    def collect_info(self):
        self.select_locale_and_keymap()

        os.system('clear')
        print(f"\n{Style.BLUE}=== Disk Selection ==={Style.RESET}")
        disks = System.list_disks()
        for i, d in enumerate(disks):
            print(f" [{i}] /dev/{d['name']} ({d['size']})")
        
        try:
            sel = int(input(f"{Style.WARN}Select disk: {Style.RESET}"))
            self.disk = f"/dev/{disks[sel]['name']}"
        except:
            sys.exit(1)

        print(f"\n{Style.BLUE}=== Security ==={Style.RESET}")
        use_luks_input = input("Enable LUKS Encryption? (yes/no): ").lower()
        if use_luks_input == "yes":
            self.use_luks = True
            while True:
                p1 = getpass.getpass("Enter encryption password: ")
                p2 = getpass.getpass("Confirm password: ")
                if p1 == p2 and p1:
                    self.luks_password = p1
                    break
                print("Mismatch.")
        else:
            self.use_luks = False

        print(f"\n{Style.BLUE}=== User Account ==={Style.RESET}")
        self.username = input("Username: ") or "tocauser"
        self.password = getpass.getpass("User Password: ")

        print(f"\n{Style.FAIL}{Style.BOLD}WARNING: ALL DATA ON {self.disk} WILL BE LOST!{Style.RESET}")
        if input("Type 'yes' to install: ") != 'yes':
            sys.exit(0)

    def partition_disk(self):
        print(f"\n{Style.BLUE}Partitioning...{Style.RESET}")
        System.run(f"wipefs -a {self.disk}", shell=True)
        
        cmds = [
            ["parted", "-s", self.disk, "mklabel", "gpt"],
            ["parted", "-s", self.disk, "mkpart", "ESP", "fat32", "1MB", "513MB"],
            ["parted", "-s", self.disk, "set", "1", "esp", "on"],
            ["parted", "-s", self.disk, "mkpart", "primary", "btrfs", "513MB", "100%"]
        ]
        for c in cmds: System.run(c)
        
        time.sleep(2)

        if "nvme" in self.disk:
            self.efi_part = f"{self.disk}p1"
            self.root_part = f"{self.disk}p2"
        else:
            self.efi_part = f"{self.disk}1"
            self.root_part = f"{self.disk}2"

    def setup_luks_if_enabled(self):
        if self.use_luks:
            print(f"\n{Style.BLUE}Encrypting Drive (LUKS2)...{Style.RESET}")
            cmd = f"cryptsetup luksFormat --type luks2 --pbkdf pbkdf2 {self.root_part} -"
            System.run(cmd, shell=True, input_text=self.luks_password+"\n"+self.luks_password)
            
            cmd_open = f"cryptsetup open {self.root_part} {MAPPER_NAME} -"
            System.run(cmd_open, shell=True, input_text=self.luks_password)
            self.root_device_final = f"/dev/mapper/{MAPPER_NAME}"
        else:
            print(f"\n{Style.BLUE}Skipping encryption...{Style.RESET}")
            self.root_device_final = self.root_part

    def format_btrfs(self):
        print(f"\n{Style.BLUE}Formatting BTRFS...{Style.RESET}")
        System.run(["mkfs.vfat", "-F32", self.efi_part])
        System.run(["mkfs.btrfs", "-f", "-L", "TocaRoot", self.root_device_final])

        tmp = "/mnt/tmp_btrfs_setup"
        os.makedirs(tmp, exist_ok=True)
        System.run(["mount", self.root_device_final, tmp])
        
        subvols = ["@", "@home", "@snapshots", "@var_log"]
        for sv in subvols:
            System.run(["btrfs", "subvolume", "create", f"{tmp}/{sv}"])
        
        System.run(["umount", tmp])
        os.rmdir(tmp)

    def mount_targets(self):
        print(f"\n{Style.BLUE}Mounting...{Style.RESET}")
        if os.path.exists(MOUNT_POINT):
            subprocess.run(["umount", "-R", MOUNT_POINT], stderr=subprocess.DEVNULL)
        
        os.makedirs(MOUNT_POINT, exist_ok=True)

        opts = "defaults,compress=zstd:1,noatime"
        System.run(["mount", "-o", f"{opts},subvol=@", self.root_device_final, MOUNT_POINT])
        
        os.makedirs(f"{MOUNT_POINT}/home", exist_ok=True)
        System.run(["mount", "-o", f"{opts},subvol=@home", self.root_device_final, f"{MOUNT_POINT}/home"])
        
        os.makedirs(f"{MOUNT_POINT}/boot/efi", exist_ok=True)
        System.run(["mount", self.efi_part, f"{MOUNT_POINT}/boot/efi"])

    def copy_system(self):
        print(f"\n{Style.BLUE}Copying RootFS...{Style.RESET}")
        cmd = [
            "rsync", "-aHAX", "--info=progress2",
            "--exclude=/dev/*", "--exclude=/proc/*", "--exclude=/sys/*", "--exclude=/tmp/*",
            f"{SOURCE_ROOTFS}/", f"{MOUNT_POINT}/"
        ]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"{Style.FAIL}Copy failed{Style.RESET}")
            sys.exit(1)

    def configure_system(self):
        print(f"\n{Style.BLUE}Configuring System...{Style.RESET}")
        
        for mp in ["/dev", "/dev/pts", "/proc", "/sys", "/run"]:
            System.run(["mount", "--bind", mp, f"{MOUNT_POINT}{mp}"])
        
        shutil.copy("/etc/resolv.conf", f"{MOUNT_POINT}/etc/resolv.conf")

        root_uuid = System.get_uuid(self.root_device_final)
        efi_uuid = System.get_uuid(self.efi_part)
        
        fstab_content = f"UUID={root_uuid} / btrfs defaults,compress=zstd:1,subvol=@ 0 0\n"
        fstab_content += f"UUID={root_uuid} /home btrfs defaults,compress=zstd:1,subvol=@home 0 0\n"
        fstab_content += f"UUID={efi_uuid} /boot/efi vfat defaults 0 2\n"
        System.write_file(f"{MOUNT_POINT}/etc/fstab", fstab_content)

        repo_deb_url = "https://raw.githubusercontent.com/NextFerretDUR/repo1/main/nfdurh.deb"
        
        luks_setup = ""
        if self.use_luks:
            luks_setup = f"""
echo "Installing cryptsetup..."
apt-get install -y cryptsetup-initramfs
RAW_UUID=$(blkid -s UUID -o value {self.root_part})
echo "{MAPPER_NAME} UUID=$RAW_UUID none luks,discard" > /etc/crypttab
"""
        
        keyboard_setup = f"""
echo "Configuring keyboard layout..."
cat <<EOF > /etc/default/keyboard
# KEYBOARD CONFIGURATION FILE
XKBMODEL="pc105"
XKBLAYOUT="{self.keymap}"
XKBVARIANT=""
XKBOPTIONS=""
BACKSPACE="guess"
EOF
"""

        setup_script = f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

apt-get update

echo "Installing NetworkManager..."
apt-get install -y network-manager

echo "Installing STRICTLY Minimal X Server and other base packages..."
apt-get install -y --no-install-recommends xserver-xorg-core xserver-xorg-video-all xserver-xorg-input-all xinit xterm x11-xserver-utils wget ca-certificates grub-efi-amd64-signed shim-signed console-setup

echo "Downloading and Installing custom .deb..."
wget -q "{repo_deb_url}" -O /tmp/nfdurh.deb
dpkg -i /tmp/nfdurh.deb || apt-get install -f -y

echo "Creating user..."
useradd -m -s /bin/bash {self.username}
echo "{self.username}:{self.password}" | chpasswd
echo "root:{self.password}" | chpasswd
usermod -aG sudo {self.username} || true

echo "exec xterm" > /home/{self.username}/.xinitrc
chown {self.username}:{self.username} /home/{self.username}/.xinitrc

{luks_setup}
{keyboard_setup}

echo "Configuring locale..."
echo "{self.selected_locale} UTF-8" > /etc/locale.gen
locale-gen
update-locale LANG={self.selected_locale}

echo "{self.hostname}" > /etc/hostname

echo "Updating initramfs..."
update-initramfs -u -k all

echo "Installing GRUB..."
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=TocaLinux --recheck
update-grub

systemctl enable NetworkManager

rm /tmp/nfdurh.deb
rm /setup_internal.sh
"""
        System.write_file(f"{MOUNT_POINT}/setup_internal.sh", setup_script)
        System.run(["chmod", "+x", f"{MOUNT_POINT}/setup_internal.sh"])

        print(f"{Style.WARN}Entering Chroot to run setup script...{Style.RESET}")
        try:
            process = subprocess.Popen(["chroot", MOUNT_POINT, "/setup_internal.sh"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in iter(process.stdout.readline, ''):
                print(line, end='')
            process.stdout.close()
            return_code = process.wait()
            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, "chroot")
        except subprocess.CalledProcessError:
            print(f"{Style.FAIL}Chroot setup script failed.{Style.RESET}")
            print(f"{Style.WARN}System is still mounted at {MOUNT_POINT} for inspection.{Style.RESET}")
            sys.exit(1)
    
    def finalize(self):
        print(f"\n{Style.BLUE}Finalizing...{Style.RESET}")
        
        if os.path.exists("/etc/NetworkManager/system-connections/"):
            os.makedirs(f"{MOUNT_POINT}/etc/NetworkManager/system-connections/", exist_ok=True)
            System.run(f"cp -r /etc/NetworkManager/system-connections/* {MOUNT_POINT}/etc/NetworkManager/system-connections/", shell=True, check=False)
            System.run(f"chmod 600 {MOUNT_POINT}/etc/NetworkManager/system-connections/*", shell=True, check=False)

        for mp in reversed(["/dev", "/dev/pts", "/proc", "/sys", "/run"]):
            subprocess.run(["umount", f"{MOUNT_POINT}{mp}"], stderr=subprocess.DEVNULL)
        
        subprocess.run(["umount", "-R", MOUNT_POINT], stderr=subprocess.DEVNULL)
        
        if self.use_luks:
            subprocess.run(["cryptsetup", "close", MAPPER_NAME], stderr=subprocess.DEVNULL)

        print(f"\n{Style.GREEN}{Style.BOLD}COMPLETE! You can now reboot the system.{Style.RESET}")

    def run(self):
        self.header()
        self.check_environment()
        self.setup_network()
        self.collect_info()
        self.partition_disk()
        self.setup_luks_if_enabled()
        self.format_btrfs()
        self.mount_targets()
        self.copy_system()
        self.configure_system()
        self.finalize()

if __name__ == "__main__":
    try:
        installer = TocaInstaller()
        installer.run()
    except KeyboardInterrupt:
        print(f"\n{Style.FAIL}Installation aborted by user.{Style.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Style.FAIL}An unexpected error occurred: {e}{Style.RESET}")
        sys.exit(1)

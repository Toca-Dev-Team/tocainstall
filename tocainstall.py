#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import shutil
import time
import getpass

# O ponto de montagem para a instalação.
MOUNT_POINT = "/mnt/toca_install"
# O nome para o dispositivo mapeado pelo LUKS.
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
        """Executa um comando de sistema e retorna a saída."""
        try:
            # Usamos subprocess.run para mais controle.
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
            # Imprime o erro para facilitar o debug, mas retorna None.
            print(f"{Style.FAIL}Erro executando comando: {' '.join(cmd)}{Style.RESET}", file=sys.stderr)
            print(f"{Style.FAIL}Stderr: {e.stderr.strip()}{Style.RESET}", file=sys.stderr)
            if check:
                # Se 'check' for True, a exceção original interromperia o fluxo.
                # Para manter a consistência do retorno, podemos relançar ou sair.
                # Por ora, vamos retornar None como o bloco try original faria.
                return None
            return None


    @staticmethod
    def list_disks():
        """Lista os discos disponíveis usando lsblk em formato JSON."""
        output = System.run(["lsblk", "-d", "-n", "-o", "NAME,SIZE,MODEL,TYPE", "-J"])
        if output:
            data = json.loads(output)
            # Filtra para mostrar apenas dispositivos do tipo 'disk'.
            return [d for d in data['blockdevices'] if d['type'] == 'disk']
        return []

    @staticmethod
    def write_file(path, content, append=False):
        """Escreve conteúdo em um arquivo."""
        mode = "a" if append else "w"
        with open(path, mode) as f:
            f.write(content)

    @staticmethod
    def get_uuid(device):
        """Obtém o UUID de um dispositivo."""
        return System.run(f"blkid -s UUID -o value {device}", shell=True)

class TocaInstaller:
    def __init__(self):
        # Configurações do disco e sistema
        self.disk = ""
        self.efi_part = ""
        self.root_part = ""
        self.luks_password = ""
        self.root_device_final = ""
        self.use_luks = False

        # Informações do sistema a ser instalado
        self.suite = "bookworm"  # Suíte Debian (ex: bookworm, bullseye)
        self.mirror = "http://deb.debian.org/debian" # Repositório para o debootstrap
        
        self.selected_locale = "en_US.UTF-8"
        self.username = "tocauser"
        self.password = "toca"
        self.hostname = "toca-machine"

    def header(self):
        os.system('clear')
        print(f"{Style.HEADER}{Style.BOLD}")
        print("  ┌──────────────────────────────────────────────────┐")
        print("  │             TOCA LINUX INSTALLER               │")
        print("  └──────────────────────────────────────────────────┘")
        print(f"{Style.RESET}")

    def check_environment(self):
        """Verifica se o ambiente de execução tem os pré-requisitos."""
        if os.geteuid() != 0:
            print(f"{Style.FAIL}Este script precisa ser executado como root.{Style.RESET}")
            sys.exit(1)
        
        # Ferramentas necessárias para a instalação, incluindo debootstrap.
        required_tools = ["debootstrap", "mkfs.btrfs", "parted", "nmcli", "wget", "cryptsetup"]
        missing_tools = [tool for tool in required_tools if shutil.which(tool) is None]

        if missing_tools:
            for tool in missing_tools:
                print(f"{Style.FAIL}Ferramenta necessária não encontrada: {tool}{Style.RESET}")
            sys.exit(1)

    def setup_network(self):
        """Menu interativo para configurar a rede."""
        while True:
            os.system('clear')
            print(f"{Style.BLUE}=== Configuração de Rede ==={Style.RESET}")
            print(f"Hostname: {Style.GREEN}{self.hostname}{Style.RESET}")
            
            ip_info = System.run("ip -br a | grep UP | awk '{print $1 \" \" $3}'", shell=True)
            print(f"Interfaces Ativas:\n{ip_info if ip_info else 'Nenhuma'}")
            
            print("\n1. Definir Hostname")
            print("2. Configurar Interface de Rede (Wi-Fi/Ethernet)")
            print("3. Testar Conectividade (Ping)")
            print("4. Continuar para Seleção de Disco")
            
            choice = input(f"\n{Style.WARN}Escolha uma opção [1-4]: {Style.RESET}")
            
            if choice == '1':
                new_host = input("Digite o novo hostname: ").strip()
                if new_host: self.hostname = new_host
            
            elif choice == '2':
                self.configure_interface()
            
            elif choice == '3':
                print("Pingando google.com...")
                if os.system("ping -c 3 google.com > /dev/null 2>&1") == 0:
                    print(f"{Style.GREEN}Conexão com a internet bem-sucedida.{Style.RESET}")
                else:
                    print(f"{Style.FAIL}Sem conexão com a internet.{Style.RESET}")
                input("Pressione Enter para continuar...")
                
            elif choice == '4':
                # Testa a conectividade antes de sair
                if os.system("ping -c 1 google.com > /dev/null 2>&1") != 0:
                    print(f"{Style.FAIL}Conexão com a internet é necessária para continuar.{Style.RESET}")
                    input("Pressione Enter para tentar novamente...")
                else:
                    break

    def configure_interface(self):
        """Configura uma interface de rede específica usando nmcli."""
        devs_raw = System.run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "dev"])
        if not devs_raw: return
        
        devs = [line.split(':') for line in devs_raw.split('\n') if line]
        
        print(f"\n{Style.BLUE}Interfaces Disponíveis:{Style.RESET}")
        for i, d in enumerate(devs):
            print(f" [{i}] {d[0]} ({d[1]}) - {d[2]}")
            
        try:
            sel = int(input("Selecione a interface: "))
            device, dev_type = devs[sel][0], devs[sel][1]
        except (ValueError, IndexError):
            print("Seleção inválida.")
            return

        if dev_type == "wifi":
            self.configure_wifi(device)
        else:
            print(f"Tentando conectar automaticamente em {device}...")
            System.run(["nmcli", "dev", "connect", device])
            time.sleep(2)

    def configure_wifi(self, device):
        """Configura uma conexão Wi-Fi."""
        print(f"Escaneando redes em {device}...")
        System.run(["nmcli", "dev", "wifi", "rescan"])
        time.sleep(3)
        
        out = System.run(["nmcli", "-t", "-f", "SSID,SECURITY,BARS", "dev", "wifi", "list"])
        if not out:
            print("Nenhuma rede encontrada.")
            return

        networks, seen = [], set()
        for line in out.split('\n'):
            parts = line.split(':')
            if len(parts) >= 2 and parts[0] and parts[0] not in seen:
                networks.append(line)
                seen.add(parts[0])

        for i, net in enumerate(networks):
            print(f" [{i}] {net.replace(':', ' | ')}")

        try:
            sel = int(input("Selecione a Rede: "))
            ssid = networks[sel].split(':')[0]
            pwd = getpass.getpass(f"Senha para {ssid}: ")
            if pwd:
                System.run(["nmcli", "dev", "wifi", "connect", ssid, "password", pwd])
            else: # Rede aberta
                System.run(["nmcli", "dev", "wifi", "connect", ssid])
        except (ValueError, IndexError):
            print("Seleção inválida.")
        except Exception as e:
            print(f"Falha ao conectar: {e}")
        time.sleep(2)

    def collect_info(self):
        """Coleta informações do usuário: disco, LUKS e credenciais."""
        print(f"\n{Style.BLUE}=== Seleção de Disco ==={Style.RESET}")
        disks = System.list_disks()
        for i, d in enumerate(disks):
            print(f" [{i}] /dev/{d['name']} ({d['size']}) - {d.get('model', 'N/A')}")
        
        try:
            sel = int(input(f"{Style.WARN}Selecione o disco para a instalação: {Style.RESET}"))
            self.disk = f"/dev/{disks[sel]['name']}"
        except (ValueError, IndexError):
            print(f"{Style.FAIL}Seleção inválida. Saindo.{Style.RESET}")
            sys.exit(1)

        print(f"\n{Style.BLUE}=== Criptografia de Disco (LUKS) ==={Style.RESET}")
        use_luks_input = input("Habilitar criptografia de disco completo (LUKS)? (s/n): ").lower()
        if use_luks_input == "s":
            self.use_luks = True
            while True:
                p1 = getpass.getpass("Digite a senha de criptografia: ")
                p2 = getpass.getpass("Confirme a senha: ")
                if p1 and p1 == p2:
                    self.luks_password = p1
                    break
                print(f"{Style.FAIL}Senhas não conferem ou estão vazias. Tente novamente.{Style.RESET}")
        else:
            self.use_luks = False

        print(f"\n{Style.BLUE}=== Conta de Usuário ==={Style.RESET}")
        self.username = input(f"Nome de usuário [{self.username}]: ") or self.username
        while True:
            p1 = getpass.getpass("Senha do usuário: ")
            p2 = getpass.getpass("Confirme a senha: ")
            if p1 and p1 == p2:
                self.password = p1
                break
            print(f"{Style.FAIL}Senhas não conferem ou estão vazias. Tente novamente.{Style.RESET}")

        print(f"\n{Style.FAIL}{Style.BOLD}AVISO: TODOS OS DADOS EM {self.disk} SERÃO PERMANENTEMENTE APAGADOS!{Style.RESET}")
        if input("Digite 'sim' para confirmar e iniciar a instalação: ") != 'sim':
            print("Instalação cancelada.")
            sys.exit(0)

    def partition_disk(self):
        """Particiona o disco selecionado (GPT com partição EFI e Raiz)."""
        print(f"\n{Style.BLUE}Particionando {self.disk}...{Style.RESET}")
        System.run(f"wipefs -a {self.disk}", shell=True)
        System.run(f"parted -s {self.disk} mklabel gpt", shell=True)
        System.run(f"parted -s {self.disk} mkpart ESP fat32 1MiB 513MiB", shell=True)
        System.run(f"parted -s {self.disk} set 1 esp on", shell=True)
        System.run(f"parted -s {self.disk} mkpart primary 513MiB 100%", shell=True)

        # Aguarda o kernel reconhecer as novas partições
        time.sleep(2)
        
        # Determina o nome das partições (ex: sda1 vs nvme0n1p1)
        if "nvme" in self.disk:
            self.efi_part = f"{self.disk}p1"
            self.root_part = f"{self.disk}p2"
        else:
            self.efi_part = f"{self.disk}1"
            self.root_part = f"{self.disk}2"

    def setup_luks_if_enabled(self):
        """Configura a criptografia LUKS na partição raiz, se habilitado."""
        if self.use_luks:
            print(f"\n{Style.BLUE}Criptografando partição raiz com LUKS2...{Style.RESET}")
            # Formata a partição com LUKS
            cmd_format = f"cryptsetup luksFormat --type luks2 --pbkdf pbkdf2 {self.root_part} -"
            System.run(cmd_format, shell=True, input_text=self.luks_password)
            
            # Abre o container LUKS para formatação
            cmd_open = f"cryptsetup open {self.root_part} {MAPPER_NAME} -"
            System.run(cmd_open, shell=True, input_text=self.luks_password)
            self.root_device_final = f"/dev/mapper/{MAPPER_NAME}"
        else:
            self.root_device_final = self.root_part

    def format_btrfs(self):
        """Formata as partições e cria os subvolumes BTRFS."""
        print(f"\n{Style.BLUE}Formatando partições e criando subvolumes BTRFS...{Style.RESET}")
        System.run(["mkfs.vfat", "-F32", self.efi_part])
        System.run(["mkfs.btrfs", "-f", "-L", "TocaRoot", self.root_device_final])

        # Montagem temporária para criar os subvolumes
        tmp_mount = "/mnt/tmp_btrfs"
        os.makedirs(tmp_mount, exist_ok=True)
        System.run(["mount", self.root_device_final, tmp_mount])
        
        subvols = ["@", "@home", "@snapshots", "@var_log"]
        for sv in subvols:
            System.run(["btrfs", "subvolume", "create", f"{tmp_mount}/{sv}"])
        
        System.run(["umount", tmp_mount])
        shutil.rmtree(tmp_mount)

    def mount_targets(self):
        """Monta os subvolumes BTRFS e a partição EFI no ponto de montagem final."""
        print(f"\n{Style.BLUE}Montando o sistema de arquivos final...{Style.RESET}")
        if os.path.exists(MOUNT_POINT):
            # Tenta desmontar recursivamente caso esteja montado de uma execução anterior
            subprocess.run(["umount", "-R", MOUNT_POINT], stderr=subprocess.DEVNULL)
        else:
            os.makedirs(MOUNT_POINT)

        opts = "defaults,compress=zstd:1,noatime,space_cache=v2,discard=async"
        System.run(["mount", "-o", f"{opts},subvol=@", self.root_device_final, MOUNT_POINT])
        
        # Cria os diretórios para os pontos de montagem aninhados
        os.makedirs(f"{MOUNT_POINT}/home", exist_ok=True)
        os.makedirs(f"{MOUNT_POINT}/boot/efi", exist_ok=True)
        
        System.run(["mount", "-o", f"{opts},subvol=@home", self.root_device_final, f"{MOUNT_POINT}/home"])
        System.run(["mount", self.efi_part, f"{MOUNT_POINT}/boot/efi"])

    def bootstrap_system(self):
        """Usa debootstrap para baixar e instalar um sistema Debian base."""
        print(f"\n{Style.BLUE}Instalando sistema base Debian ({self.suite}) via debootstrap...{Style.RESET}")
        print(f"Isso pode levar vários minutos, dependendo da sua conexão com a internet.")
        cmd = [
            "debootstrap",
            "--arch=amd64",
            "--variant=minbase",
            self.suite,
            MOUNT_POINT,
            self.mirror
        ]
        try:
            # Usamos subprocess.run diretamente para mostrar a saída em tempo real
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            print(f"{Style.FAIL}Falha no debootstrap. Verifique a conexão com a internet e o espelho do repositório.{Style.RESET}")
            sys.exit(1)

    def configure_system(self):
        """Configura o sistema base instalado (fstab, chroot, pacotes, etc.)."""
        print(f"\n{Style.BLUE}Configurando o sistema instalado...{Style.RESET}")
        
        # Monta sistemas de arquivos virtuais para o chroot funcionar corretamente
        for mp in ["/dev", "/dev/pts", "/proc", "/sys"]:
            System.run(["mount", "--bind", mp, f"{MOUNT_POINT}{mp}"])
        
        # Copia a configuração de DNS para dentro do chroot para que a rede funcione
        shutil.copy("/etc/resolv.conf", f"{MOUNT_POINT}/etc/resolv.conf")

        # Gera o /etc/fstab
        root_uuid = System.get_uuid(self.root_device_final)
        efi_uuid = System.get_uuid(self.efi_part)
        fstab_opts = "defaults,compress=zstd:1,noatime,space_cache=v2,discard=async"
        fstab_content = (
            f"UUID={root_uuid} / btrfs {fstab_opts},subvol=@ 0 0\n"
            f"UUID={root_uuid} /home btrfs {fstab_opts},subvol=@home 0 0\n"
            f"UUID={efi_uuid} /boot/efi vfat defaults 0 2\n"
        )
        System.write_file(f"{MOUNT_POINT}/etc/fstab", fstab_content)

        # URL do pacote .deb customizado
        repo_deb_url = "https://raw.githubusercontent.com/NextFerretDUR/repo1/main/nfdurh.deb"
        
        # Bloco de configuração para LUKS, se habilitado
        luks_setup = ""
        if self.use_luks:
            luks_setup = f"""
# Configurando LUKS para o boot
echo "Instalando cryptsetup-initramfs..."
apt-get install -y cryptsetup-initramfs
RAW_UUID=$(blkid -s UUID -o value {self.root_part})
echo "Criando /etc/crypttab..."
echo "{MAPPER_NAME} UUID=$RAW_UUID none luks,discard,initramfs" > /etc/crypttab
"""

        # Script que será executado dentro do chroot
        setup_script = f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive

# Configura o sources.list para o sistema base
echo "deb {self.mirror} {self.suite} main contrib non-free non-free-firmware" > /etc/apt/sources.list
apt-get update

# Instala pacotes essenciais e de hardware
echo "Instalando kernel, sudo e ferramentas de rede..."
apt-get install -y linux-image-amd64 sudo network-manager firmware-linux wget ca-certificates

# Instala o ambiente gráfico mínimo e o bootloader
echo "Instalando X.Org mínimo e GRUB..."
apt-get install -y --no-install-recommends xserver-xorg-core xserver-xorg-video-all xserver-xorg-input-all xinit xterm x11-xserver-utils grub-efi-amd64-signed shim-signed

# Baixa e instala o pacote .deb customizado
echo "Baixando e instalando pacote customizado..."
wget -q "{repo_deb_url}" -O /tmp/nfdurh.deb
dpkg -i /tmp/nfdurh.deb || apt-get install -f -y

# Cria o usuário e define senhas
echo "Criando usuário {self.username}..."
useradd -m -s /bin/bash -G sudo {self.username}
echo "{self.username}:{self.password}" | chpasswd
echo "root:{self.password}" | chpasswd

# Configura um .xinitrc básico para o usuário
echo "exec xterm" > /home/{self.username}/.xinitrc
chown {self.username}:{self.username} /home/{self.username}/.xinitrc

# Executa a configuração do LUKS se necessário
{luks_setup}

# Atualiza initramfs e instala o GRUB
echo "Finalizando configuração do bootloader..."
update-initramfs -u -k all
grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=TocaLinux --recheck
update-grub

# Configura locale e hostname
echo "Configurando locale e hostname..."
echo "{self.selected_locale} UTF-8" > /etc/locale.gen
locale-gen
echo "LANG={self.selected_locale}" > /etc/default/locale
echo "{self.hostname}" > /etc/hostname

# Habilita o NetworkManager
systemctl enable NetworkManager

# Limpeza
rm -f /tmp/nfdurh.deb /setup_internal.sh
"""
        System.write_file(f"{MOUNT_POINT}/setup_internal.sh", setup_script)
        System.run(["chmod", "+x", f"{MOUNT_POINT}/setup_internal.sh"])

        print(f"{Style.WARN}Entrando no chroot para finalizar a configuração...{Style.RESET}")
        try:
            subprocess.run(["chroot", MOUNT_POINT, "/setup_internal.sh"], check=True)
        except subprocess.CalledProcessError:
            print(f"{Style.FAIL}A configuração via chroot falhou. O sistema pode estar inconsistente.{Style.RESET}")
            # Não saia imediatamente, permita a finalização para que o usuário possa inspecionar.
    
    def finalize(self):
        """Finaliza a instalação, copia configurações de rede e desmonta tudo."""
        print(f"\n{Style.BLUE}Finalizando e limpando...{Style.RESET}")
        
        # Copia as conexões de rede ativas para o novo sistema
        nm_connections_path = "/etc/NetworkManager/system-connections/"
        target_nm_path = f"{MOUNT_POINT}/etc/NetworkManager/system-connections/"
        if os.path.exists(nm_connections_path):
            print("Copiando configurações de rede para o novo sistema...")
            os.makedirs(target_nm_path, exist_ok=True)
            System.run(f"cp -r {nm_connections_path}* {target_nm_path}", shell=True, check=False)
            System.run(f"chmod 600 {target_nm_path}*", shell=True, check=False)

        # Desmonta todos os sistemas de arquivos
        print("Desmontando sistemas de arquivos...")
        subprocess.run(["umount", "-R", MOUNT_POINT], stderr=subprocess.DEVNULL)
        
        # Fecha o container LUKS se foi usado
        if self.use_luks:
            print("Fechando container LUKS...")
            subprocess.run(["cryptsetup", "close", MAPPER_NAME], stderr=subprocess.DEVNULL)

        print(f"\n{Style.GREEN}{Style.BOLD}INSTALAÇÃO COMPLETA!{Style.RESET}")
        print("Você agora pode reiniciar o sistema.")

    def run(self):
        """Executa todas as etapas do instalador em ordem."""
        try:
            self.header()
            self.check_environment()
            self.setup_network()
            self.collect_info()
            self.partition_disk()
            self.setup_luks_if_enabled()
            self.format_btrfs()
            self.mount_targets()
            self.bootstrap_system() # Mudança aqui
            self.configure_system()
            self.finalize()
        except KeyboardInterrupt:
            print(f"\n{Style.WARN}Instalação interrompida pelo usuário.{Style.RESET}")
            # Tenta limpar antes de sair
            self.finalize()
            sys.exit(1)
        except Exception as e:
            print(f"\n{Style.FAIL}Um erro inesperado ocorreu: {e}{Style.RESET}")
            # Tenta limpar
            self.finalize()
            sys.exit(1)


if __name__ == "__main__":
    installer = TocaInstaller()
    installer.run()

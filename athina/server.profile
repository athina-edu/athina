# generic server profile
noblacklist /sbin
noblacklist /bin/nc
noblacklist /usr/sbin
whitelist /tmp/athina
whitelist /tmp/athina-test

blacklist ${HOME}/.pki/nssdb
blacklist ${HOME}/.lastpass
blacklist ${HOME}/.keepassx
blacklist ${HOME}/.password-store
# noroot # need root for suid apps
private
private-dev

# Root sys privelleges
# caps.drop all

# Network operations
#seccomp
# protocol unix,inet,inet6,netlink,packet
netfilter

# History files in $HOME
blacklist-nolog ${HOME}/.*_history
blacklist-nolog ${HOME}/.adobe
blacklist-nolog ${HOME}/.bash_history
blacklist-nolog ${HOME}/.history
blacklist-nolog ${HOME}/.local/share/fish/fish_history
blacklist-nolog ${HOME}/.macromedia

# X11 session autostart
# blacklist ${HOME}/.xpra - this will kill --x11=xpra cmdline option for all programs
blacklist ${HOME}/.Xsession
blacklist ${HOME}/.config/autostart
blacklist ${HOME}/.config/autostart-scripts
blacklist ${HOME}/.config/lxsession/LXDE/autostart
blacklist ${HOME}/.config/openbox/autostart
blacklist ${HOME}/.config/openbox/environment
blacklist ${HOME}/.config/plasma-workspace/env
blacklist ${HOME}/.config/plasma-workspace/shutdown
blacklist ${HOME}/.config/startupconfig
blacklist ${HOME}/.fluxbox/startup
blacklist ${HOME}/.gnomerc
blacklist ${HOME}/.kde/Autostart
blacklist ${HOME}/.kde/env
blacklist ${HOME}/.kde/share/autostart
blacklist ${HOME}/.kde/share/config/startupconfig
blacklist ${HOME}/.kde/shutdown
blacklist ${HOME}/.kde4/env
blacklist ${HOME}/.kde4/Autostart
blacklist ${HOME}/.kde4/share/autostart
blacklist ${HOME}/.kde4/shutdown
blacklist ${HOME}/.kde4/share/config/startupconfig
blacklist ${HOME}/.local/share/autostart
blacklist ${HOME}/.xinitrc
blacklist ${HOME}/.xprofile
blacklist ${HOME}/.xserverrc
blacklist ${HOME}/.xsession
blacklist ${HOME}/.xsessionrc
blacklist /etc/X11/Xsession.d
blacklist /etc/xdg/autostart

# KDE config
blacklist ${HOME}/.config/*.notifyrc
blacklist ${HOME}/.config/khotkeysrc
blacklist ${HOME}/.config/krunnerrc
blacklist ${HOME}/.config/plasma-org.kde.plasma.desktop-appletsrc
blacklist ${HOME}/.kde/share/apps/konsole
blacklist ${HOME}/.kde/share/apps/kwin
blacklist ${HOME}/.kde/share/apps/plasma
blacklist ${HOME}/.kde/share/apps/solid
blacklist ${HOME}/.kde/share/config/*.notifyrc
blacklist ${HOME}/.kde/share/config/khotkeysrc
blacklist ${HOME}/.kde/share/config/krunnerrc
blacklist ${HOME}/.kde/share/config/plasma-desktop-appletsrc
blacklist ${HOME}/.kde4/share/apps/plasma
blacklist ${HOME}/.kde4/share/apps/konsole
blacklist ${HOME}/.kde4/share/apps/kwin
blacklist ${HOME}/.kde4/share/config/krunnerrc
blacklist ${HOME}/.kde4/share/config/plasma-desktop-appletsrc
blacklist ${HOME}/.kde4/share/config/khotkeysrc
blacklist ${HOME}/.kde4/share/apps/solid
blacklist ${HOME}/.kde4/share/config/*.notifyrc
blacklist ${HOME}/.local/share/kglobalaccel
blacklist ${HOME}/.local/share/konsole
blacklist ${HOME}/.local/share/kwin
blacklist ${HOME}/.local/share/plasma
blacklist ${HOME}/.local/share/solid
read-only ${HOME}/.config/kdeglobals
read-only ${HOME}/.kde/share/config/kdeglobals
read-only ${HOME}/.kde/share/kde4/services
read-only ${HOME}/.kde4/share/kde4/services
read-only ${HOME}/.kde4/share/config/kdeglobals
read-only ${HOME}/.local/share/kservices5

# systemd
blacklist ${HOME}/.config/systemd
blacklist ${HOME}/.local/share/systemd

# VirtualBox
blacklist ${HOME}/.VirtualBox
blacklist ${HOME}/.config/VirtualBox
blacklist ${HOME}/VirtualBox VMs

# VeraCrypt
blacklist ${HOME}/.VeraCrypt
blacklist ${PATH}/veracrypt
blacklist ${PATH}/veracrypt-uninstall.sh
blacklist /usr/share/applications/veracrypt.*
blacklist /usr/share/pixmaps/veracrypt.*
blacklist /usr/share/veracrypt

# TrueCrypt
blacklist ${HOME}/.TrueCrypt
blacklist ${PATH}/truecrypt
blacklist ${PATH}/truecrypt-uninstall.sh
blacklist /usr/share/applications/truecrypt.*
blacklist /usr/share/pixmaps/truecrypt.*
blacklist /usr/share/truecrypt

# zuluCrypt
blacklist ${HOME}/.zuluCrypt
blacklist ${HOME}/.zuluCrypt-socket
blacklist ${PATH}/zuluCrypt-cli
blacklist ${PATH}/zuluMount-cli

# var
blacklist /var/cache/apt
blacklist /var/cache/pacman
blacklist /var/lib/apt
blacklist /var/lib/clamav
blacklist /var/lib/dkms
blacklist /var/lib/mysql/mysql.sock
blacklist /var/lib/mysqld/mysql.sock
blacklist /var/lib/pacman
blacklist /var/lib/systemd
blacklist /var/lib/upower
blacklist /var/log
blacklist /var/mail
blacklist /var/opt
blacklist /var/run/acpid.socket
blacklist /var/run/docker.sock
blacklist /var/run/minissdpd.sock
blacklist /var/run/mysql/mysqld.sock
blacklist /var/run/mysqld/mysqld.sock
blacklist /var/run/rpcbind.sock
blacklist /var/run/screens
blacklist /var/run/systemd
blacklist /var/spool/anacron
blacklist /var/spool/cron

# etc
blacklist /etc/anacrontab
blacklist /etc/cron*
blacklist /etc/profile.d
blacklist /etc/rc.local

# Startup files
read-only ${HOME}/.antigen
read-only ${HOME}/.bash_aliases
read-only ${HOME}/.bash_login
read-only ${HOME}/.bash_logout
read-only ${HOME}/.bash_profile
read-only ${HOME}/.bashrc
read-only ${HOME}/.config/fish
read-only ${HOME}/.csh_files
read-only ${HOME}/.cshrc
read-only ${HOME}/.forward
read-only ${HOME}/.local/share/fish
read-only ${HOME}/.login
read-only ${HOME}/.logout
read-only ${HOME}/.pam_environment
read-only ${HOME}/.pgpkey
read-only ${HOME}/.plan
read-only ${HOME}/.profile
read-only ${HOME}/.project
read-only ${HOME}/.tcshrc
read-only ${HOME}/.zlogin
read-only ${HOME}/.zlogout
read-only ${HOME}/.zprofile
read-only ${HOME}/.zsh.d
read-only ${HOME}/.zsh_files
read-only ${HOME}/.zshenv
read-only ${HOME}/.zshrc
read-only ${HOME}/.zshrc.local

# Initialization files that allow arbitrary command execution
read-only ${HOME}/.caffrc
read-only ${HOME}/.dotfiles
read-only ${HOME}/.emacs
read-only ${HOME}/.emacs.d
read-only ${HOME}/.exrc
read-only ${HOME}/.gvimrc
read-only ${HOME}/.iscreenrc
read-only ${HOME}/.mailcap
read-only ${HOME}/.msmtprc
read-only ${HOME}/.mutt/muttrc
read-only ${HOME}/.muttrc
read-only ${HOME}/.nano
read-only ${HOME}/.reportbugrc
read-only ${HOME}/.tmux.conf
read-only ${HOME}/.vim
read-only ${HOME}/.vimrc
read-only ${HOME}/.xmonad
read-only ${HOME}/.xscreensaver
read-only ${HOME}/_exrc
read-only ${HOME}/_gvimrc
read-only ${HOME}/_vimrc
read-only ${HOME}/dotfiles

# Make directories commonly found in $PATH read-only
read-only ${HOME}/.gem
read-only ${HOME}/.luarocks
read-only ${HOME}/.npm-packages
read-only ${HOME}/bin

# The following block breaks trash functionality in file managers
#read-only  ${HOME}/.local
#read-write ${HOME}/.local/share
#noexec     ${HOME}/.local/share
blacklist   ${HOME}/.local/share/Trash

# Write-protection for desktop entries
read-only ${HOME}/.local/share/applications

# top secret
blacklist ${HOME}/*.kdb
blacklist ${HOME}/*.kdbx
blacklist ${HOME}/*.key
blacklist ${HOME}/.Private
blacklist ${HOME}/.caff
blacklist ${HOME}/.cert
blacklist ${HOME}/.config/keybase
blacklist ${HOME}/.ecryptfs
blacklist ${HOME}/.gnome2/keyrings
blacklist ${HOME}/.gnupg
blacklist ${HOME}/.kde/share/apps/kwallet
blacklist ${HOME}/.kde4/share/apps/kwallet
blacklist ${HOME}/.local/share/keyrings
blacklist ${HOME}/.local/share/kwalletd
blacklist ${HOME}/.msmtprc
blacklist ${HOME}/.mutt/muttrc
blacklist ${HOME}/.muttrc
blacklist ${HOME}/.netrc
blacklist ${HOME}/.pki
blacklist ${HOME}/.password-store
blacklist ${HOME}/.smbcredentials
blacklist ${HOME}/.ssh
blacklist /etc/group+
blacklist /etc/group-
blacklist /etc/gshadow
blacklist /etc/gshadow+
blacklist /etc/gshadow-
blacklist /etc/passwd+
blacklist /etc/passwd-
blacklist /etc/shadow
blacklist /etc/shadow+
blacklist /etc/shadow-
blacklist /etc/ssh
blacklist /home/.ecryptfs
blacklist /var/backup

# system directories
# blacklist /sbin
# blacklist /usr/local/sbin
# blacklist /usr/sbin

# system management
blacklist ${PATH}/at
blacklist ${PATH}/chage
blacklist ${PATH}/chfn
blacklist ${PATH}/chsh
blacklist ${PATH}/crontab
blacklist ${PATH}/evtest
blacklist ${PATH}/expiry
blacklist ${PATH}/fusermount
blacklist ${PATH}/gpasswd
blacklist ${PATH}/ksu
blacklist ${PATH}/mount
blacklist ${PATH}/mount.ecryptfs_private
blacklist ${PATH}/nc
blacklist ${PATH}/ncat
blacklist ${PATH}/newgidmap
blacklist ${PATH}/newgrp
blacklist ${PATH}/newuidmap
blacklist ${PATH}/ntfs-3g
blacklist ${PATH}/pkexec
blacklist ${PATH}/procmail
blacklist ${PATH}/sg
blacklist ${PATH}/strace
blacklist ${PATH}/su
blacklist ${PATH}/sudo
blacklist ${PATH}/umount
blacklist ${PATH}/unix_chkpwd
blacklist ${PATH}/xev
blacklist ${PATH}/xinput

# other SUID binaries
blacklist /usr/lib/virtualbox
blacklist /usr/lib64/virtualbox

# prevent lxterminal connecting to an existing lxterminal session
blacklist /tmp/.lxterminal-socket*
# prevent tmux connecting to an existing session
blacklist /tmp/tmux-*

# disable terminals running as server resulting in sandbox escape
blacklist ${PATH}/gnome-terminal
blacklist ${PATH}/gnome-terminal.wrapper
blacklist ${PATH}/lilyterm
blacklist ${PATH}/mate-terminal
blacklist ${PATH}/mate-terminal.wrapper
blacklist ${PATH}/pantheon-terminal
blacklist ${PATH}/roxterm
blacklist ${PATH}/roxterm-config
blacklist ${PATH}/terminix
blacklist ${PATH}/tilix
blacklist ${PATH}/urxvtc
blacklist ${PATH}/urxvtcd
blacklist ${PATH}/xfce4-terminal
blacklist ${PATH}/xfce4-terminal.wrapper
# blacklist ${PATH}/konsole
# konsole doesn't seem to have this problem - last tested on Ubuntu 16.04

# kernel files
blacklist /initrd*
blacklist /vmlinuz*

# complement noexec ${HOME} and noexec /tmp
# noexec /tmp/.X11-unix

blacklist ${HOME}/.config/KeePass
blacklist ${HOME}/.config/keepass
blacklist ${HOME}/.config/keepassx
blacklist ${HOME}/.config/keepassxc
blacklist ${HOME}/.keepass
blacklist ${HOME}/.keepassx
blacklist ${HOME}/.keepassxc
blacklist ${HOME}/.lastpass
blacklist ${HOME}/.local/share/KeePass
blacklist ${HOME}/.local/share/keepass
blacklist ${HOME}/.password-store

blacklist ${HOME}/.*coin
blacklist ${HOME}/.8pecxstudios
blacklist ${HOME}/.AndroidStudio*
blacklist ${HOME}/.Atom
blacklist ${HOME}/.FBReader
blacklist ${HOME}/.FontForge
blacklist ${HOME}/.IdeaIC*
blacklist ${HOME}/.LuminanceHDR
blacklist ${HOME}/.Mathematica
blacklist ${HOME}/.Natron
blacklist ${HOME}/.Skype
blacklist ${HOME}/.Steam
blacklist ${HOME}/.Steampath
blacklist ${HOME}/.Steampid
blacklist ${HOME}/.TelegramDesktop
blacklist ${HOME}/.VirtualBox
blacklist ${HOME}/.Wolfram Research
blacklist ${HOME}/.android
blacklist ${HOME}/.arduino15
blacklist ${HOME}/.atom
blacklist ${HOME}/.attic
blacklist ${HOME}/.audacity-data
blacklist ${HOME}/.bcast5
blacklist ${HOME}/.bibletime
blacklist ${HOME}/.claws-mail
blacklist ${HOME}/.config/0ad
blacklist ${HOME}/.config/2048-qt
blacklist ${HOME}/.config/Atom
blacklist ${HOME}/.config/Audaciousrc
blacklist ${HOME}/.config/Brackets
blacklist ${HOME}/.config/Clementine
blacklist ${HOME}/.config/Cryptocat
blacklist ${HOME}/.config/Franz
blacklist ${HOME}/.config/Gitter
blacklist ${HOME}/.config/Google
blacklist ${HOME}/.config/Gpredict
blacklist ${HOME}/.config/INRIA
blacklist ${HOME}/.config/InSilmaril
blacklist ${HOME}/.config/Luminance
blacklist ${HOME}/.config/Meltytech
blacklist ${HOME}/.config/Mousepad
blacklist ${HOME}/.config/Mumble
blacklist ${HOME}/.config/MusE
blacklist ${HOME}/.config/MuseScore
blacklist ${HOME}/.config/Nylas Mail
blacklist ${HOME}/.config/Qlipper
blacklist ${HOME}/.config/QuiteRss
blacklist ${HOME}/.config/QuiteRssrc
blacklist ${HOME}/.config/Riot
blacklist ${HOME}/.config/Slack
blacklist ${HOME}/.config/Thunar
blacklist ${HOME}/.config/VirtualBox
blacklist ${HOME}/.config/Wire
blacklist ${HOME}/.config/akregatorrc
blacklist ${HOME}/.config/ardour4
blacklist ${HOME}/.config/ardour5
blacklist ${HOME}/.config/arkrc
blacklist ${HOME}/.config/atril
blacklist ${HOME}/.config/audacious
blacklist ${HOME}/.config/aweather
blacklist ${HOME}/.config/baloofilerc
blacklist ${HOME}/.config/baloorc
blacklist ${HOME}/.config/blender
blacklist ${HOME}/.config/bless
blacklist ${HOME}/.config/borg
blacklist ${HOME}/.config/brasero
blacklist ${HOME}/.config/brave
blacklist ${HOME}/.config/caja
blacklist ${HOME}/.config/calibre
blacklist ${HOME}/.config/catfish
blacklist ${HOME}/.config/cherrytree
blacklist ${HOME}/.config/chromium
blacklist ${HOME}/.config/chromium-dev
blacklist ${HOME}/.config/chromium-flags.conf
blacklist ${HOME}/.config/clipit
blacklist ${HOME}/.config/cmus
blacklist ${HOME}/.config/darktable
blacklist ${HOME}/.config/deadbeef
blacklist ${HOME}/.config/deluge
blacklist ${HOME}/.config/digikam
blacklist ${HOME}/.config/dolphinrc
blacklist ${HOME}/.config/dragonplayerrc
blacklist ${HOME}/.config/enchant
blacklist ${HOME}/.config/eog
blacklist ${HOME}/.config/epiphany
blacklist ${HOME}/.config/evince
blacklist ${HOME}/.config/evolution
blacklist ${HOME}/.config/filezilla
blacklist ${HOME}/.config/flowblade
blacklist ${HOME}/.config/gajim
blacklist ${HOME}/.config/galculator
blacklist ${HOME}/.config/geany
blacklist ${HOME}/.config/gedit
blacklist ${HOME}/.config/geeqie
blacklist ${HOME}/.config/ghb
blacklist ${HOME}/.config/globaltime
blacklist ${HOME}/.config/google-chrome
blacklist ${HOME}/.config/google-chrome-beta
blacklist ${HOME}/.config/google-chrome-unstable
blacklist ${HOME}/.config/gpicview
blacklist ${HOME}/.config/gthumb
blacklist ${HOME}/.config/gwenviewrc
blacklist ${HOME}/.config/hexchat
blacklist ${HOME}/.config/inox
blacklist ${HOME}/.config/itch
blacklist ${HOME}/.config/jd-gui.cfg
blacklist ${HOME}/.config/k3brc
blacklist ${HOME}/.config/katepartrc
blacklist ${HOME}/.config/katerc
blacklist ${HOME}/.config/kateschemarc
blacklist ${HOME}/.config/katesyntaxhighlightingrc
blacklist ${HOME}/.config/katevirc
blacklist ${HOME}/.config/kdeconnect
blacklist ${HOME}/.config/knotesrc
blacklist ${HOME}/.config/ktorrentrc
blacklist ${HOME}/.config/leafpad
blacklist ${HOME}/.config/libreoffice
blacklist ${HOME}/.config/lximage-qt
blacklist ${HOME}/.config/mate-calc
blacklist ${HOME}/.config/mate/eom
blacklist ${HOME}/.config/mate/mate-dictionary
blacklist ${HOME}/.config/midori
blacklist ${HOME}/.config/mpv
blacklist ${HOME}/.config/mupen64plus
blacklist ${HOME}/.config/nautilus
blacklist ${HOME}/.config/nemo
blacklist ${HOME}/.config/netsurf
blacklist ${HOME}/.config/okularpartrc
blacklist ${HOME}/.config/okularrc
blacklist ${HOME}/.config/opera
blacklist ${HOME}/.config/opera-beta
blacklist ${HOME}/.config/orage
blacklist ${HOME}/.config/org.kde.gwenviewrc
blacklist ${HOME}/.config/pcmanfm
blacklist ${HOME}/.config/pix
blacklist ${HOME}/.config/pluma
blacklist ${HOME}/.config/psi+
blacklist ${HOME}/.config/qBittorrent
blacklist ${HOME}/.config/qBittorrentrc
blacklist ${HOME}/.config/qpdfview
blacklist ${HOME}/.config/qt5ct
blacklist ${HOME}/.config/qupzilla
blacklist ${HOME}/.config/qutebrowser
blacklist ${HOME}/.config/ranger
blacklist ${HOME}/.config/redshift.conf
blacklist ${HOME}/.config/ristretto
blacklist ${HOME}/.config/scribus
blacklist ${HOME}/.config/skypeforlinux
blacklist ${HOME}/.config/slimjet
blacklist ${HOME}/.config/smplayer
blacklist ${HOME}/.config/spotify
blacklist ${HOME}/.config/stellarium
blacklist ${HOME}/.config/synfig
blacklist ${HOME}/.config/telepathy-account-widgets
blacklist ${HOME}/.config/torbrowser
blacklist ${HOME}/.config/totem
blacklist ${HOME}/.config/tox
blacklist ${HOME}/.config/transmission
blacklist ${HOME}/.config/uGet
blacklist ${HOME}/.config/viewnior
blacklist ${HOME}/.config/vivaldi
blacklist ${HOME}/.config/vlc
blacklist ${HOME}/.config/wesnoth
blacklist ${HOME}/.config/wire
blacklist ${HOME}/.config/wireshark
blacklist ${HOME}/.config/xchat
blacklist ${HOME}/.config/xed
blacklist ${HOME}/.config/xfburn
blacklist ${HOME}/.config/xfce4/xfce4-notes.gtkrc
blacklist ${HOME}/.config/xfce4/xfce4-notes.rc
blacklist ${HOME}/.config/xfce4/xfconf/xfce-perchannel-xml/thunar.xml
blacklist ${HOME}/.config/xfce4-dict
blacklist ${HOME}/.config/xiaoyong
blacklist ${HOME}/.config/xmms2
blacklist ${HOME}/.config/xplayer
blacklist ${HOME}/.config/xreader
blacklist ${HOME}/.config/xviewer
blacklist ${HOME}/.config/yandex-browser
blacklist ${HOME}/.config/yandex-browser-beta
blacklist ${HOME}/.config/zathura
blacklist ${HOME}/.config/zoomus.conf
blacklist ${HOME}/.conkeror.mozdev.org
blacklist ${HOME}/.curlrc
blacklist ${HOME}/.dia
blacklist ${HOME}/.dillo
blacklist ${HOME}/.dosbox
blacklist ${HOME}/.dropbox-dist
blacklist ${HOME}/.electrum*
blacklist ${HOME}/.elinks
blacklist ${HOME}/.emacs
blacklist ${HOME}/.emacs
blacklist ${HOME}/.emacs.d
blacklist ${HOME}/.etr
blacklist ${HOME}/.filezilla
blacklist ${HOME}/.flowblade
blacklist ${HOME}/.fltk
blacklist ${HOME}/.frozen-bubble
blacklist ${HOME}/.gimp*
blacklist ${HOME}/.git-credential-cache
blacklist ${HOME}/.gitconfig
blacklist ${HOME}/.googleearth/Cache/
blacklist ${HOME}/.googleearth/Temp/
blacklist ${HOME}/.googleearth/myplaces.backup.kml
blacklist ${HOME}/.googleearth/myplaces.kml
blacklist ${HOME}/.gradle
blacklist ${HOME}/.guayadeque
blacklist ${HOME}/.hedgewars
blacklist ${HOME}/.hugin
blacklist ${HOME}/.icedove
blacklist ${HOME}/.inkscape
blacklist ${HOME}/.java
blacklist ${HOME}/.jitsi
blacklist ${HOME}/.kde/share/apps/gwenview
blacklist ${HOME}/.kde/share/apps/kcookiejar
blacklist ${HOME}/.kde/share/apps/khtml
blacklist ${HOME}/.kde/share/apps/konqsidebartng
blacklist ${HOME}/.kde/share/apps/konqueror
blacklist ${HOME}/.kde/share/apps/okular
blacklist ${HOME}/.kde/share/config/baloofilerc
blacklist ${HOME}/.kde/share/config/baloorc
blacklist ${HOME}/.kde/share/config/digikam
blacklist ${HOME}/.kde/share/config/gwenviewrc
blacklist ${HOME}/.kde/share/config/k3brc
blacklist ${HOME}/.kde/share/config/kcookiejarrc
blacklist ${HOME}/.kde/share/config/khtmlrc
blacklist ${HOME}/.kde/share/config/konq_history
blacklist ${HOME}/.kde/share/config/konqsidebartngrc
blacklist ${HOME}/.kde/share/config/konquerorrc
blacklist ${HOME}/.kde/share/config/ktorrentrc
blacklist ${HOME}/.kde/share/config/okularpartrc
blacklist ${HOME}/.kde/share/config/okularrc
blacklist ${HOME}/.kde4/share/config/baloorc
blacklist ${HOME}/.kde4/share/config/baloofilerc
blacklist ${HOME}/.kde4/share/apps/okular
blacklist ${HOME}/.kde4/share/apps/konqueror
blacklist ${HOME}/.kde4/share/apps/konqsidebartng
blacklist ${HOME}/.kde4/share/apps/khtml
blacklist ${HOME}/.kde4/share/apps/kcookiejar
blacklist ${HOME}/.kde4/share/config/digikam
blacklist ${HOME}/.kde4/share/apps/gwenview
blacklist ${HOME}/.kde4/share/config/kcookiejarrc
blacklist ${HOME}/.kde4/share/config/khtmlrc
blacklist ${HOME}/.kde4/share/config/konq_history
blacklist ${HOME}/.kde4/share/config/konqsidebartngrc
blacklist ${HOME}/.kde4/share/config/konquerorrc
blacklist ${HOME}/.kde4/share/config/okularpartrc
blacklist ${HOME}/.kde4/share/config/okularrc
blacklist ${HOME}/.kde4/share/config/ktorrentrc
blacklist ${HOME}/.kde4/share/config/gwenviewrc
blacklist ${HOME}/.kde4/share/config/k3brc
blacklist ${HOME}/.killingfloor
blacklist ${HOME}/.kino-history
blacklist ${HOME}/.kinorc
blacklist ${HOME}/.kodi
blacklist ${HOME}/.linphone-history.db
blacklist ${HOME}/.linphonerc
blacklist ${HOME}/.lmmsrc.xml
blacklist ${HOME}/.local/.share/maps-places.json
blacklist ${HOME}/.local/lib/python2.7/site-packages
blacklist ${HOME}/.local/share/0ad
blacklist ${HOME}/.local/share/3909/PapersPlease
blacklist ${HOME}/.local/share/Empathy
blacklist ${HOME}/.local/share/JetBrains
blacklist ${HOME}/.local/share/Mumble
blacklist ${HOME}/.local/share/QuiteRss
blacklist ${HOME}/.local/share/Ricochet
blacklist ${HOME}/.local/share/Steam
blacklist ${HOME}/.local/share/SuperHexagon
blacklist ${HOME}/.local/share/Terraria
blacklist ${HOME}/.local/share/TpLogger
blacklist ${HOME}/.local/share/akregator
blacklist ${HOME}/.local/share/aspyr-media
blacklist ${HOME}/.local/share/baloo
blacklist ${HOME}/.local/share/caja-python
blacklist ${HOME}/.local/share/cdprojektred
blacklist ${HOME}/.local/share/clipit
blacklist ${HOME}/.local/share/data/Mumble
blacklist ${HOME}/.local/share/data/MusE
blacklist ${HOME}/.local/share/data/MuseScore
blacklist ${HOME}/.local/share/dino
blacklist ${HOME}/.local/share/dolphin
blacklist ${HOME}/.local/share/epiphany
blacklist ${HOME}/.local/share/evolution
blacklist ${HOME}/.local/share/feral-interactive
blacklist ${HOME}/.local/share/gajim
blacklist ${HOME}/.local/share/geary
blacklist ${HOME}/.local/share/geeqie
blacklist ${HOME}/.local/share/gnome-2048
blacklist ${HOME}/.local/share/gnome-chess
blacklist ${HOME}/.local/share/gnome-music
blacklist ${HOME}/.local/share/gnome-photos
blacklist ${HOME}/.local/share/gwenview
blacklist ${HOME}/.local/share/kate
blacklist ${HOME}/.local/share/ktorrentrc
blacklist ${HOME}/.local/share/kwrite
blacklist ${HOME}/.local/share/lollypop
blacklist ${HOME}/.local/share/meld
blacklist ${HOME}/.local/share/multimc
blacklist ${HOME}/.local/share/multimc5
blacklist ${HOME}/.local/share/mupen64plus
blacklist ${HOME}/.local/share/nautilus
blacklist ${HOME}/.local/share/nautilus-python
blacklist ${HOME}/.local/share/nemo
blacklist ${HOME}/.local/share/nemo-python
blacklist ${HOME}/.local/share/notes
blacklist ${HOME}/.local/share/okular
blacklist ${HOME}/.local/share/orage
blacklist ${HOME}/.local/share/org.kde.gwenview
blacklist ${HOME}/.local/share/pix
blacklist ${HOME}/.local/share/psi+
blacklist ${HOME}/.local/share/qpdfview
blacklist ${HOME}/.local/share/scribus
blacklist ${HOME}/.local/share/spotify
blacklist ${HOME}/.local/share/steam
blacklist ${HOME}/.local/share/supertux2
blacklist ${HOME}/.local/share/telepathy
blacklist ${HOME}/.local/share/torbrowser
blacklist ${HOME}/.local/share/totem
blacklist ${HOME}/.local/share/vpltd
blacklist ${HOME}/.local/share/vulkan
blacklist ${HOME}/.local/share/wesnoth
blacklist ${HOME}/.local/share/xplayer
blacklist ${HOME}/.local/share/xreader
blacklist ${HOME}/.local/share/zathura
blacklist ${HOME}/.lv2
blacklist ${HOME}/.mcabber
blacklist ${HOME}/.mcabberrc
blacklist ${HOME}/.mediathek3
blacklist ${HOME}/.minetest
blacklist ${HOME}/.mozilla
blacklist ${HOME}/.mpdconf
blacklist ${HOME}/.mplayer
blacklist ${HOME}/.msmtprc
blacklist ${HOME}/.multimc5
blacklist ${HOME}/.mutt
blacklist ${HOME}/.mutt/muttrc
blacklist ${HOME}/.muttrc
blacklist ${HOME}/.neverball
blacklist ${HOME}/.nv
blacklist ${HOME}/.nylas-mail
blacklist ${HOME}/.openinvaders
blacklist ${HOME}/.openshot
blacklist ${HOME}/.openshot_qt
blacklist ${HOME}/.opera
blacklist ${HOME}/.opera-beta
blacklist ${HOME}/.pingus
blacklist ${HOME}/.purple
blacklist ${HOME}/.qemu-launcher
blacklist ${HOME}/.remmina
blacklist ${HOME}/.retroshare
blacklist ${HOME}/.scribus
blacklist ${HOME}/.scribusrc
blacklist ${HOME}/.simutrans
blacklist ${HOME}/.steam
blacklist ${HOME}/.steampath
blacklist ${HOME}/.steampid
blacklist ${HOME}/.stellarium
blacklist ${HOME}/.subversion
blacklist ${HOME}/.sword
blacklist ${HOME}/.sylpheed-2.0
blacklist ${HOME}/.synfig
blacklist ${HOME}/.tconn
blacklist ${HOME}/.thunderbird
blacklist ${HOME}/.tooling
blacklist ${HOME}/.ts3client
blacklist ${HOME}/.tuxguitar*
blacklist ${HOME}/.unknow-horizons
blacklist ${HOME}/.viking
blacklist ${HOME}/.viking-maps
blacklist ${HOME}/.vst
blacklist ${HOME}/.w3m
blacklist ${HOME}/.warzone2100-3.*
blacklist ${HOME}/.weechat
blacklist ${HOME}/.wgetrc
blacklist ${HOME}/.wine
blacklist ${HOME}/.wine64
blacklist ${HOME}/.xiphos
blacklist ${HOME}/.xmms
blacklist ${HOME}/.xonotic
blacklist ${HOME}/.xpdfrc
blacklist ${HOME}/.zoom
blacklist ${HOME}/wallet.dat
blacklist /tmp/ssh-*

# ~/.cache directory
blacklist ${HOME}/.cache/0ad
blacklist ${HOME}/.cache/8pecxstudios
blacklist ${HOME}/.cache/Franz
blacklist ${HOME}/.cache/INRIA
blacklist ${HOME}/.cache/QuiteRss
blacklist ${HOME}/.cache/attic
blacklist ${HOME}/.cache/borg
blacklist ${HOME}/.cache/calibre
blacklist ${HOME}/.cache/champlain
blacklist ${HOME}/.cache/chromium
blacklist ${HOME}/.cache/chromium-dev
blacklist ${HOME}/.cache/darktable
blacklist ${HOME}/.cache/epiphany
blacklist ${HOME}/.cache/evolution
blacklist ${HOME}/.cache/gajim
blacklist ${HOME}/.cache/geeqie
blacklist ${HOME}/.cache/google-chrome
blacklist ${HOME}/.cache/google-chrome-beta
blacklist ${HOME}/.cache/google-chrome-unstable
blacklist ${HOME}/.cache/icedove
blacklist ${HOME}/.cache/inox
blacklist ${HOME}/.cache/libgweather
blacklist ${HOME}/.cache/midori
blacklist ${HOME}/.cache/mozilla
blacklist ${HOME}/.cache/mutt
blacklist ${HOME}/.cache/netsurf
blacklist ${HOME}/.cache/opera
blacklist ${HOME}/.cache/opera-beta
blacklist ${HOME}/.cache/org.gnome.Books
blacklist ${HOME}/.cache/peek
blacklist ${HOME}/.cache/qBittorrent
blacklist ${HOME}/.cache/qupzilla
blacklist ${HOME}/.cache/qutebrowser
blacklist ${HOME}/.cache/simple-scan
blacklist ${HOME}/.cache/slimjet
blacklist ${HOME}/.cache/spotify
blacklist ${HOME}/.cache/telepathy
blacklist ${HOME}/.cache/thunderbird
blacklist ${HOME}/.cache/torbrowser
blacklist ${HOME}/.cache/transmission
blacklist ${HOME}/.cache/vivaldi
blacklist ${HOME}/.cache/wesnoth
blacklist ${HOME}/.cache/xmms2
blacklist ${HOME}/.cache/xreader
blacklist ${HOME}/.cache/yandex-browser
blacklist ${HOME}/.cache/yandex-browser-beta


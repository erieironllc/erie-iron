export ERIELAB_ENV=dev

alias dj='python manage.py $1'
alias f='find . -type f -name'
alias ns='cd /home/collaya-msg-processor-1/src/erielab-webservice && source ./env/bin/activate'
alias ll='ls -alhF'
alias la='ls -A'
alias l='ls -CF'
alias eb='sudo vi /usr/local/bin/baremetal-bash-aliases.sh && source ~/.bashrc'
alias tl_deploy='journalctl -t baremetal-container-manager -f'
alias tl_msg_processor='bash /usr/local/bin/psw.sh'

function fe() {
    # Check if a search pattern was provided
    if [ -z "$1" ]; then
        echo "Usage: fe <search_pattern>"
        return 1
    fi

    # Use find to locate files that start with the given pattern
    # -type f ensures that only regular files are considered
    # -name "${1}*" matches files starting with the pattern
    local file
    file=$(find . -type f -name "${1}*" -print -quit)

    if [ -n "$file" ]; then
        # Open the first matched file with vi
        vi "$file"
    else
        echo "No files found matching pattern: $1"
    fi
}
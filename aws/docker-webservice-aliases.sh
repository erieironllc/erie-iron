#!/bin/bash
alias ll='ls -l'
alias psql_db='psql -h erielab-db.cn84ymg4qp1p.us-west-2.rds.amazonaws.com -U postgres -p 5432'
alias eb='vi ~/.bashrc && source ~/.bashrc'
alias run_ingest_benchmark='python manage.py run_ingest_benchmark'


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


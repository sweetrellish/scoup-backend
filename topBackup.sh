#!/bin/bash
# Recursively backs up all files in all subdirectories
# Requires ./create_backup.sh to be in the same directory

backup_files() {
  local dir="$1"
  
  for file in "$dir"/*; do
    # Skip if file doesn't exist (handles empty directories)
    [ -e "$file" ] || continue
    
    if [ -f "$file" ]; then
      ./create_backup.sh "$file"
    elif [ -d "$file" ]; then
      backup_files "$file"
    fi
  done
}

backup_files "."

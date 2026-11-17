import os
import shutil
from pathlib import Path

PREVIOUSLY_DONE_PATH = Path("previously_done.txt")

def save_job_signature(signature):
    """Append a job signature to previously_done.txt"""
    with open(PREVIOUSLY_DONE_PATH, "a", encoding="utf-8") as f:
        f.write(f"{signature}\n")


def extract_job_signature_from_folder(folder_name: str) -> str:
    """
    Extract job signature from folder name.
    Format: YYYY-MM-DD_Company_JobTitle
    Returns: Company_JobTitle (lowercase, normalized)
    """
    parts = folder_name.split('_', 1)
    if len(parts) == 2:
        # Remove the date prefix and return the rest as signature
        return parts[1].lower()
    return folder_name.lower()


def organize_applications():
    base_path = Path.cwd()
    
    apps_dir = base_path / "_applications"
    ready_dir = base_path / "ready"
    still_dir = base_path / "still"
    trash_dir = base_path / "duplicates_trash"

    if not apps_dir.exists():
        print(f"Error: Could not find the _applications folder at {apps_dir}")
        return

    ready_dir.mkdir(parents=True, exist_ok=True)
    still_dir.mkdir(parents=True, exist_ok=True)
    trash_dir.mkdir(parents=True, exist_ok=True)

    print("Scanning and scoring applications...")

    # Step 1: Group all folders by their exact Job Signature
    jobs_dict = {}

    for folder in apps_dir.iterdir():
        if not folder.is_dir():
            continue

        parts = folder.name.split('_', 1)
        job_signature = parts[1].lower() if len(parts) == 2 else folder.name.lower()

        if job_signature not in jobs_dict:
            jobs_dict[job_signature] = []
        jobs_dict[job_signature].append(folder)

    # Step 2: Evaluate duplicates and pick the most complete one
    for signature, folders in jobs_dict.items():
        
        if len(folders) > 1:
            # We have duplicates! Score them to find the most complete one.
            def score_folder(f):
                files = [x.name.lower() for x in f.iterdir() if x.is_file()]
                has_cv = any("cv.pdf" in x for x in files)
                has_anschreiben = any("anschreiben.pdf" in x for x in files)
                has_link = any("apply_data.txt" in x for x in files)
                return has_cv + has_anschreiben + has_link # Max score is 3

            # Sort the folders so the highest score is first in the list
            folders.sort(key=score_folder, reverse=True)
            
            best_folder = folders[0]
            duplicates_to_trash = folders[1:]
            
            # Trash the inferior/incomplete versions
            for dup in duplicates_to_trash:
                print(f"[-] DELETING INCOMPLETE DUPLICATE: {dup.name}")
                shutil.move(str(dup), str(trash_dir / dup.name))
        else:
            # No duplicates, just grab the only folder
            best_folder = folders[0]

        # Step 3: Sort the winning folder into Ready or Still
        files = [x.name.lower() for x in best_folder.iterdir() if x.is_file()]
        has_cv = any("cv.pdf" in x for x in files)
        has_anschreiben = any("anschreiben.pdf" in x for x in files)
        has_link = any("apply_data.txt" in x for x in files)

        if has_cv and has_anschreiben and has_link:
            print(f"[+] READY: {best_folder.name}")
            shutil.move(str(best_folder), str(ready_dir / best_folder.name))
            # ── RECORD IN HISTORY LEDGER ──────────────────────────────────────
            signature = extract_job_signature_from_folder(best_folder.name)
            save_job_signature(signature)
            
        elif has_cv and not has_anschreiben:
            print(f"[~] STILL: {best_folder.name}")
            shutil.move(str(best_folder), str(still_dir / best_folder.name))
            
        else:
            print(f"[?] UNKNOWN STATE: {best_folder.name} (Leaving in _applications)")

    print("\nOrganization complete! Check your 'ready' and 'still' folders.")

if __name__ == "__main__":
    organize_applications()
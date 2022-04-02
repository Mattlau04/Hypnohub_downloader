# -*- coding: utf-8 -*-
# @Author: Mattlau04
# @Date:   2022-03-25 16:26:58
# @Last Modified by:   Mattlau04
# @Last Modified time: 2022-04-02 14:02:03

########## CONFIG ##########
SAVE_METADATA = True # Whether to save post metadatas in json files
SAVE_IMAGES = True # Whether to save post images files
SAVE_THUMBNAILS = False # If True, saves thumbnails instead of full res, 
# saving space in exchange of some loss of quality

THREAD_COUNT = 30 # Number of threads used for scrapping / downloading
RETRY_COUNT = 100 # How many times to retry downloading a post
###########################

# This is the start of the actual code, you probably
# shouldn't edit anything here
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from os.path import exists
from queue import Empty, Queue
from time import sleep
from typing import List, Tuple, Union

import regex
import requests


@dataclass
class Post:
    """Represents a hypnohub post"""
    id: int
    metadata: dict
    """The raw metadata of the post"""
    image_url: Union[str, None]
    """
    Either a url to the fullres or thumbnail, depending on SAVE_THUMBNAILS
    Can be None for deleted posts.
    """
    md5: str
    """used in filenames"""


def clear() -> None:
    """Clears the terminal"""
    os.system('cls' if os.name == 'nt' else 'clear')

def pretty_timedelta(td: timedelta) -> str:
    hours, remainder = divmod(round(td.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'

status_thread_running: bool = False
def status_thread(successful_downloads: List[Post], failed_downloads: List[Post], 
                    skipped_downloads: List[Post], total_posts: int, start: datetime):
    """Updates the status of the scrapping"""
    while status_thread_running:
        term_width = min(os.get_terminal_size().columns, 50)
        total_downloaded = len(successful_downloads) + len(failed_downloads) + len(skipped_downloads)
        progress = total_downloaded / total_posts
        elapsed = datetime.now() - start
        try:
            eta = ((elapsed/ (total_downloaded) ) * total_posts) - elapsed
            #eta = ( (elapsed / (total_downloaded-len(skipped_downloads)) ) * (total_posts-len(skipped_downloads))) - elapsed
        except ZeroDivisionError:
            eta = timedelta(0)
        clear()
        print(f"[{('█'*round(progress * (term_width+2) )).ljust(term_width-2)}]")
        print(f" {round(progress*100, 2)}% complete")
        print('-'*term_width)
        print(f'Elapsed: {pretty_timedelta(elapsed)}')
        print(f'ETA: {pretty_timedelta(eta)}')
        print('-'*term_width)
        print(f'Successful: {len(successful_downloads)} / {total_posts}')
        print(f'Failed: {len(failed_downloads)} / {total_posts}')
        print(f'Skipped: {len(skipped_downloads)} / {total_posts}')
        print()
        print(f'Total: {total_downloaded} / {total_posts}')

        sleep(1)

def get_deleted_post_image(post: Post) -> str:
    """Gets a deleted post's image url"""
    if SAVE_THUMBNAILS: # the easy way
        return requests.get("https://hypnohub.net/hypnohub-hover-zoom.php", params={"post": post.id}).url
    else:
        r = requests.get(f"https://hypnohub.net/post/show/{post.id}/")
        r.raise_for_status()
        return regex.search('https:\/\/hypnohub\.net\/\/data\/image\/.{32}\.[a-zA-Z]*', r.text).group()

def get_post_queue(query: str) -> Queue[Post]:
    """
    Gets the posts in a tag and adds them to a queue
    """
    def query_fetcher_thread(thread_id: int):
        """
        Used to get posts from a query in a threaded way
        Gets every 'thread_nbr*n + thread_id' page, (with n the number of visited pages)
        so all thread will be getting unique pages. theard_id must be unique to each
        thread, start at 1 and end at thread_nbr
        """
        visited_pages = 0 # The number of page this thread visited
        posts: List[Post] = []
        with requests.Session() as s:
            while True: # Main scraping loop
                try:
                    r = s.get("https://hypnohub.net/post/index.json", timeout=10,
                            params={'limit': 1000, 'tags': query, 
                                'page': THREAD_COUNT*visited_pages + thread_id}).json()
                    if not r:
                        # Empty page, we got everything from this thread
                        return posts
                    posts.extend( Post(id=p['id'], metadata=p, md5=p['md5'],# We add all posts to the list
                                        image_url=p.get('sample_url' if SAVE_THUMBNAILS else 'file_url', None))
                                 for p in r) 
                    visited_pages +=1
                except Exception:
                    pass
   
    # First we get all post from the query, this can be a bit slow, so
    # i added some simple threading to it
    print("Getting list of post to download...")
    # we start all threads
    with ThreadPoolExecutor(max_workers=THREAD_COUNT, thread_name_prefix='query_fetcher') as pool:
        all_posts = pool.map(query_fetcher_thread, range(1, THREAD_COUNT+1))
    queue = Queue()
    all_posts = [post for postlist in all_posts for post in postlist] # We flatten the list
    print(f"Found {len(all_posts)} posts to download")
    for p in all_posts: # For all posts
        queue.put_nowait(p)
    return queue

def download_queue(queue: Queue[Post], foldername: str) -> Tuple[List[Post], List[Post], List[Post]]:
    successful_downloads: List[Post] = []
    failed_downloads: List[Post] = []
    skipped_downloads: List[Post] = []
    total_posts = queue.qsize()
    start = datetime.now()

    # Start the status thread
    global status_thread_running
    status_thread_running = True
    threading.Thread(target=status_thread, name="status_thread", 
                    args=(successful_downloads, failed_downloads, skipped_downloads, total_posts, start)
                    ).start()

    def download_posts_from_queue() -> None:
        """Downloads posts from the queue until the queue we're all done"""
        while True:
        # While there are still posts to download
            try: # We try to get a post to download
                post = queue.get_nowait()
            except Empty:
                return

            if post.image_url is None:
                post.image_url = get_deleted_post_image(post)

            filename = f"{post.id}_{post.md5}"
            image_extension = post.image_url.split('.')[-1]

            # Image downloading
            if SAVE_IMAGES:
                if not exists(f'./{foldername}/images/{filename}.{image_extension}'):
                    for try_nbr in range(RETRY_COUNT):
                        try:
                            r = requests.get(post.image_url)
                            r.raise_for_status()
                            with open(f'./{foldername}/images/{filename}.{image_extension}', 'wb+') as f:
                                f.write(r.content)
                        except Exception:
                            # We failed, ignoring and retrying
                            pass
                        else:
                            # We downloaded the post without any problem
                            #print(f"Downloaded post #{post.id}")
                            successful_downloads.append(post)
                            break
                    else:
                        # we failed all the attempts - deal with the consequences.
                        #print(f"/!\ Failed download for post #{post.id}!!! (url: {post.image_url})")
                        failed_downloads.append(post)
                else:
                    skipped_downloads.append(post)
            else:
                skipped_downloads.append(post)

            # Metadata saving
            if SAVE_METADATA:
                with open(f'./{foldername}/metadata/{filename}.json', 'w+') as f:
                    json.dump(post.metadata, f, indent=4)

            # It doesn't matter is we succeeded or not, we mark the queue as done
            queue.task_done()

    print("Starting download of all posts")
    # we start all threads
    with ThreadPoolExecutor(max_workers=THREAD_COUNT, thread_name_prefix='post_downloader') as pool:
        for _ in range(THREAD_COUNT):
            pool.submit(download_posts_from_queue)

    status_thread_running = False
    end = datetime.now()
    sleep(1.5)
    clear()
    print("Downloaded all posts")
    print(f"Success: {len(successful_downloads)}/{total_posts}")
    print(f"Failed: {len(failed_downloads)}/{total_posts}")
    print(f"Skipped: {len(skipped_downloads)}/{total_posts}")
    print()
    print(f"Finished at: {end:%d/%m/%Y %H:%M:%S}")
    print(f"Took: {pretty_timedelta(end - start)}")
    if failed_downloads:
        print('-' * 10)
        print("Failed downloads:")
        print("(Those are often corrupted posts whose image URL's 404)")
        for p in failed_downloads:
            print(f"- Post #{p.id} ({p.image_url})")

    return successful_downloads, failed_downloads, skipped_downloads


def main():
    print("""Useful metatags:
---------------
'deleted:all' (downloads both deleted and non-deleted posts)
'deleted:true' (downloads only deleted posts)
'user:username' (downloads only posts from a user)
'vote:>=3:username' (downloads only posts a user favorited)
'vote:>=1:username' (downloads only posts a user favorited or liked)
'score:>100' (only shows posts above 100 likes)
---------------""")
    query = input("Query: ").strip()
    query = ' '.join(s for s in query.split(' ') if s) # Remove all dupe spaces

    default_foldername = f'{datetime.now():%Y-%m-%d %H-%M-%S} {query[:100]}'
    default_foldername = ''.join(c for c in default_foldername if c.isalnum() or c in (' ', '_', '-')).strip()
    print('-'*10)
    print('Choose a folder name to where you want to save the posts')
    print(f"(leave empty for '{default_foldername}')")
    print("You can also put the name of an existing folder to resume download, as existing images won't be redownloaded")
    foldername = input('> ').strip()
    if not foldername:
        foldername = default_foldername

    # We gotta make the folder and check that the pics are valid
    os.makedirs(f'./{foldername}/images', exist_ok=True)
    os.makedirs(f'./{foldername}/metadata', exist_ok=True)

    print('-' * 10)
    queue = get_post_queue(query)
    successful_downloads, failed_downloads, skipped_downloads = download_queue(queue, foldername)

    print()
    print('Press enter to exit')
    input()

if __name__ == "__main__":
    main()

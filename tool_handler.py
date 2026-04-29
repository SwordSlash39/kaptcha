import json
import requests
from pathlib import Path
from bs4 import BeautifulSoup
from ddgs import DDGS
import shutil
from datetime import datetime

import threading
import queue
from playwright.sync_api import sync_playwright

# ==========================================
# SETUP VAULT DIRECTORY & STATE
# ==========================================
VAULT_DIR = Path.cwd() / "vault"
VAULT_DIR.mkdir(parents=True, exist_ok=True)

# Tracks the current working directory relative to the vault root
CURRENT_VAULT_PATH = Path(".")

def _get_safe_path(target):
    # Ensure target doesn't jump to drive root
    safe_target = str(target).lstrip('/\\') 
    
    # Resolve absolute paths to prevent ".." escaping
    base_dir = (VAULT_DIR / CURRENT_VAULT_PATH).resolve()
    final_path = (base_dir / safe_target).resolve()
    
    # Security Check
    if not str(final_path).startswith(str(VAULT_DIR.resolve())):
        raise ValueError(f"Access Denied: {final_path} is outside the /vault/ folder.")
    return final_path

# ==========================================
# PLAYWRIGHT BROWSER MANAGER (Background Thread)
# ==========================================

# Setup persistent browser data directory
BROWSER_DIR = VAULT_DIR / "browser_data"
BROWSER_DIR.mkdir(parents=True, exist_ok=True)

class BrowserManager:
    def __init__(self):
        self.req_q = queue.Queue()
        self.res_q = queue.Queue()
        self.thread = threading.Thread(target=self._run_browser, daemon=True)
        self.thread.start()

    def _run_browser(self):
        with sync_playwright() as p:
            # Bypass Bot Detection
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DIR),
                headless=False,
                viewport={"width": 1280, "height": 800},
                channel="chrome", 
                args=['--disable-blink-features=AutomationControlled'],
                ignore_default_args=['--enable-automation']
            )
            
            # Hide webdriver from Javascript
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            
            while True:
                try:
                    # NON-BLOCKING GET: Does not freeze the OS thread
                    req = self.req_q.get_nowait()
                except queue.Empty:
                    # THE FIX: Yields control back to Playwright's event loop!
                    # This allows the browser to process new tabs, network events, etc.
                    try:
                        if context.pages:
                            context.pages[0].wait_for_timeout(100)
                        else:
                            import time
                            time.sleep(0.1)
                    except Exception:
                        pass
                    continue
                
                if req is None: break
                cmd, kwargs = req

                # Strip cmd
                cmd = cmd.strip()
                
                try:
                    # 1. Initialize or update active page logic
                    if not hasattr(self, 'active_page'):
                        self.active_page = context.pages[-1] if context.pages else context.new_page()
                    
                    # 2. If the active page was closed externally
                    if self.active_page.is_closed() or self.active_page not in context.pages:
                        self.active_page = context.pages[-1] if context.pages else context.new_page()

                    # 3. Handle popups naturally: if a new tab appeared, switch to it automatically
                    current_pages = context.pages
                    if hasattr(self, 'last_pages_count') and len(current_pages) > self.last_pages_count:
                        self.active_page = current_pages[-1]
                    self.last_pages_count = len(current_pages)
                    
                    # Set the page variable for all commands to use
                    page = self.active_page

                    if cmd == "navigate":
                        url = kwargs["url"]
                        if not url.startswith("http://") and not url.startswith("https://"):
                            url = "https://" + url
                        
                        try:
                            page.goto(url, timeout=15000)
                            page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except Exception:
                            pass 
                            
                        res = f"Navigated to {page.url}"
                        
                    elif cmd == "get_view":
                        self.view_checked_since_hover = True
                        import base64, json
                        
                        # 1. Take CLEAN Screenshot
                        page.evaluate("() => document.querySelectorAll('.kaptcha-badge, #kaptcha-grid-overlay').forEach(e => e.remove())")
                        page.evaluate("() => document.querySelectorAll('[data-kaptcha-id]').forEach(el => el.style.outline = '')")
                        clean_bytes = page.screenshot(type="jpeg", quality=60)
                        
                        # 2. Inject BADGES & Take Screenshot
                        js_badges = """
                        () => {
                            let elements = document.querySelectorAll('a, button, input, textarea, select, [role="button"],[tabindex]');
                            let items =[];
                            elements.forEach((el, i) => {
                                let rect = el.getBoundingClientRect();
                                if(rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.left >= 0) {
                                    el.setAttribute('data-kaptcha-id', i);
                                    el.style.outline = '2px solid red'; 
                                    items.push({
                                        id: i,
                                        tag: el.tagName.toLowerCase(),
                                        text: (el.innerText || el.value || el.placeholder || el.ariaLabel || '').trim().substring(0, 50)
                                    });
                                    let badge = document.createElement('div');
                                    badge.className = 'kaptcha-badge';
                                    badge.innerText = i;
                                    badge.style.position = 'absolute';
                                    badge.style.top = (window.scrollY + rect.top) + 'px';
                                    badge.style.left = (window.scrollX + rect.left) + 'px';
                                    badge.style.background = '#ffeb3b';
                                    badge.style.color = '#000';
                                    badge.style.fontSize = '12px';
                                    badge.style.fontWeight = '900';
                                    badge.style.zIndex = '1147483646';
                                    badge.style.padding = '1px 4px';
                                    badge.style.border = '1px solid #000';
                                    badge.style.pointerEvents = 'none';
                                    document.body.appendChild(badge);
                                }
                            });
                            return items;
                        }
                        """
                        items = page.evaluate(js_badges)
                        badges_bytes = page.screenshot(type="jpeg", quality=60)
                        
                        # 3. Remove Badges, Inject GRID & Take Screenshot
                        js_grid = """
                        () => {
                            document.querySelectorAll('.kaptcha-badge').forEach(e => e.remove());
                            document.querySelectorAll('[data-kaptcha-id]').forEach(el => el.style.outline = '');
                            
                            let grid = document.createElement('div');
                            grid.id = 'kaptcha-grid-overlay';
                            grid.style.position = 'fixed';
                            grid.style.top = '0'; grid.style.left = '0';
                            grid.style.width = '1280px'; grid.style.height = '800px';
                            grid.style.pointerEvents = 'none';
                            grid.style.zIndex = '2147483647';
                            grid.style.display = 'grid';
                            
                            /* CHANGED: Made grid cells 80x80 */
                            grid.style.gridTemplateColumns = 'repeat(26, 50px)';
                            grid.style.gridTemplateRows = 'repeat(16, 50px)';
                            
                            const rows =['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P'];
                            for(let r=0; r<16; r++) {
                                for(let c=0; c<26; c++) {
                                    let cell = document.createElement('div');
                                    cell.style.border = '1px solid rgba(0, 255, 255, 0.4)';
                                    cell.style.boxSizing = 'border-box';
                                    cell.style.display = 'flex';
                                    cell.style.alignItems = 'center';
                                    cell.style.justifyContent = 'center';
                                    cell.style.fontSize = '18px';
                                    cell.style.color = 'rgba(0, 255, 255, 0.9)';
                                    cell.style.fontWeight = 'bold';
                                    cell.style.textShadow = '-1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000';
                                    cell.innerText = rows[r] + (c+1);
                                    grid.appendChild(cell);
                                }
                            }
                            document.body.appendChild(grid);
                            let c = document.getElementById('kaptcha-llm-cursor'); if(c) document.body.appendChild(c);
                        }
                        """
                        page.evaluate(js_grid)
                        grid_bytes = page.screenshot(type="jpeg", quality=60)
                        
                        # 4. Final Cleanup
                        page.evaluate("() => document.querySelectorAll('#kaptcha-grid-overlay').forEach(e => e.remove())")
                        
                        vp = page.viewport_size
                        res_text = f"Viewport Size: {vp['width']}x{vp['height']}\n"
                        
                        # Add hover state to the text response so the LLM knows its exact current position
                        if hasattr(self, 'hover_x') and self.hover_x is not None:
                            res_text += f"Current Hovered Position: Grid {getattr(self, 'hover_grid_id', 'Unknown')} (X={self.hover_x}, Y={self.hover_y})\n"

                        res_text += "\n".join([f"[{item['id']}] {item['tag']} - {item['text']}" for item in items if item['text']])
                        if len(items) == 0: res_text += "No ID-mapped elements found."
                        
                        b64_clean = base64.b64encode(clean_bytes).decode('utf-8')
                        b64_badges = base64.b64encode(badges_bytes).decode('utf-8')
                        b64_grid = base64.b64encode(grid_bytes).decode('utf-8')
                        
                        res = json.dumps({
                            "__kaptcha_multimodal__": True, 
                            "text": res_text, 
                            "images_b64":[b64_clean, b64_badges, b64_grid]
                        })

                    elif cmd == "click":
                        element_id = kwargs["element_id"]
                        # Reduced timeout to 2s, and post-click wait to 400ms
                        page.locator(f"[data-kaptcha-id='{element_id}']").click(force=True, timeout=2000)
                        page.wait_for_timeout(400)
                        res = f"Clicked element[{element_id}]."
                        
                    elif cmd == "type":
                        # We type directly on the keyboard. (Adding a slight 10ms delay makes it act more human)
                        page.keyboard.type(kwargs["text"], delay=10)
                        
                        if kwargs.get("press_enter", False):
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(800)
                            res = f"Typed '{kwargs['text']}' and pressed Enter."
                        else:
                            page.wait_for_timeout(400)
                            res = f"Typed '{kwargs['text']}'."
                            
                    elif cmd == "scroll":
                        amount = kwargs.get("amount", 400)
                        if kwargs["direction"] == "up": amount = -amount
                        page.mouse.wheel(0, amount)
                        page.wait_for_timeout(500)
                        res = f"Scrolled {kwargs['direction']} by {abs(amount)} pixels."

                    elif cmd == "hover_grid":
                        grid_id = kwargs["grid_id"].upper()
                        rows = "ABCDEFGHIJKLMNOP"
                        if len(grid_id) >= 2 and grid_id[0] in rows and grid_id[1:].isdigit():
                            row_idx = rows.index(grid_id[0])
                            col_idx = int(grid_id[1:]) - 1
                            
                            x = (col_idx * 50) + 25
                            y = (row_idx * 50) + 25
                            
                            self.hover_x = x
                            self.hover_y = y
                            self.hover_grid_id = grid_id
                            
                            # Lock the clicker until the LLM actually views the screen
                            self.view_checked_since_hover = False
                            
                            # Inject a highly visible bullseye pointer that is fixed to the viewport
                            js_cursor = f"""
                            () => {{
                                let cursor = document.getElementById('kaptcha-llm-cursor');
                                if (!cursor) {{
                                    cursor = document.createElement('div');
                                    cursor.id = 'kaptcha-llm-cursor';
                                    cursor.style.position = 'fixed'; // Viewport relative
                                    cursor.style.width = '50px';
                                    cursor.style.height = '50px';
                                    cursor.style.borderRadius = '50%';
                                    cursor.style.backgroundColor = '#ff0000'; // Solid Bright Red
                                    cursor.style.border = '3px solid #ffffff'; // White inner ring
                                    cursor.style.boxShadow = '0 0 0 3px #000000, 0 0 10px 5px rgba(0,0,0,0.5)'; // Black outer ring + glow
                                    cursor.style.pointerEvents = 'none'; // Don't block actual clicks!
                                    cursor.style.zIndex = '2147483647';
                                    cursor.style.transform = 'translate(-50%, -50%)'; 
                                    document.body.appendChild(cursor);
                                }}
                                cursor.style.left = '{x}px';
                                cursor.style.top = '{y}px';
                            }}
                            """
                            page.evaluate(js_cursor)
                            page.wait_for_timeout(500)
                            res = f"Hovered over grid {grid_id}. SYSTEM LOCK: You MUST run browser_get_view next to visually confirm the cursor placement. Clicking is temporarily disabled until you look."
                        else:
                            res = f"Error: Invalid grid_id '{grid_id}'. Must be Row (A-P) and Col (1-26)."

                    elif cmd == "click_hovered":
                        if not getattr(self, 'view_checked_since_hover', True):
                            res = "FATAL ERROR: You attempted to click blindly! You are REQUIRED to call browser_get_view to visually check the bullseye cursor placement before calling browser_click_hovered."
                        elif hasattr(self, 'hover_x') and self.hover_x is not None:
                            page.mouse.click(self.hover_x, self.hover_y)
                            page.wait_for_timeout(1000)
                            res = f"Clicked at hovered coordinates (X={self.hover_x}, Y={self.hover_y})."
                        else:
                            res = "Error: No coordinates hovered yet. Run browser_hover_grid first."

                    elif cmd == "press_key":
                        key = kwargs["key"]
                        page.keyboard.press(key)
                        page.wait_for_timeout(500)
                        res = f"Pressed key '{key}' on the keyboard."
                    
                    elif cmd == "list_tabs":
                        tabs_info =[]
                        for i, p in enumerate(context.pages):
                            active_str = " (ACTIVE)" if p == self.active_page else ""
                            title = p.title()[:50] if p.title() else "Loading..."
                            tabs_info.append(f"[{i}] {title} - {p.url}{active_str}")
                        res = "Open tabs:\n" + "\n".join(tabs_info) if tabs_info else "No tabs open."
                        
                    elif cmd == "open_tab":
                        new_p = context.new_page()
                        self.active_page = new_p
                        url = kwargs.get("url")
                        if url:
                            if not url.startswith("http://") and not url.startswith("https://"):
                                url = "https://" + url
                            try:
                                new_p.goto(url, timeout=15000)
                                new_p.wait_for_load_state("domcontentloaded", timeout=5000)
                            except Exception:
                                pass
                            res = f"Opened new tab[{len(context.pages)-1}] and navigated to {new_p.url}"
                        else:
                            res = f"Opened new empty tab [{len(context.pages)-1}]."

                    elif cmd == "close_tab":
                        tab_id = kwargs["tab_id"]
                        if 0 <= tab_id < len(context.pages):
                            closing_page = context.pages[tab_id]
                            closing_page.close()
                            res = f"Closed tab [{tab_id}]."
                        else:
                            res = f"Error: Tab ID {tab_id} does not exist."

                    elif cmd == "switch_tab":
                        tab_id = kwargs["tab_id"]
                        if 0 <= tab_id < len(context.pages):
                            self.active_page = context.pages[tab_id]
                            self.active_page.bring_to_front()
                            res = f"Switched to tab[{tab_id}] ({self.active_page.title()})."
                        else:
                            res = f"Error: Tab ID {tab_id} does not exist."

                except Exception as e:
                    res = f"Browser Error: {str(e)}"
                
                self.res_q.put(res)
    
    def execute(self, cmd, **kwargs):
        self.req_q.put((cmd, kwargs))
        return self.res_q.get()

# Start background browser loop safely
browser_manager = BrowserManager()

# ==========================================
# FUNCTIONS
# ==========================================
def current_datetime():
    return f"The time now is {str(datetime.now())}."
    
def web_search(query):
    """Searches the web using DuckDuckGo."""
    try:
        results = DDGS().text(query, max_results=5)
        if not results: return "No search results found."
        
        return "\n\n".join([f"{i+1}. {r['title']}\nURL: {r['href']}\nSnippet: {r['body']}" for i, r in enumerate(results)])
    except Exception as e:
        return f"Search failed: {str(e)}"

def scrape_url(url):
    """Fetches text content from a URL."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        text = soup.get_text(separator='\n', strip=True)
        return text[:4000] + "\n\n[CONTENT TRUNCATED]" if len(text) > 4000 else text
    except Exception as e:
        return f"Failed to scrape URL: {str(e)}"

def vault_get_working_directory():
    """Returns the current directory the AI is working in."""
    # CURRENT_VAULT_PATH is the global variable you are already updating 
    # in vault_change_directory
    display_path = "/vault" if str(CURRENT_VAULT_PATH) == "." else f"/vault/{CURRENT_VAULT_PATH}"
    return f"You are currently in: {display_path}"

def vault_change_directory(path):
    """Moves the AI into a subdirectory, or out using '..'"""
    global CURRENT_VAULT_PATH
    try:
        target_path = _get_safe_path(path)
        if not target_path.is_dir():
            return f"Error: Directory '{path}' does not exist."
        
        # Update current path
        rel_path = target_path.relative_to(VAULT_DIR)
        CURRENT_VAULT_PATH = rel_path
        
        display_path = "/vault" if str(CURRENT_VAULT_PATH) == "." else f"/vault/{CURRENT_VAULT_PATH}"
        return f"Success. You are now in: {display_path}"
    except Exception as e:
        return str(e)

def vault_create_directory(dirname):
    """Creates a new folder inside the current path."""
    try:
        target_path = _get_safe_path(dirname)
        target_path.mkdir(parents=True, exist_ok=True)
        return f"Successfully created directory: {dirname}"
    except Exception as e:
        return f"Error creating directory: {str(e)}"

def vault_list_files(subpath="."):
    """Lists files and directories in the target path (defaults to current directory)."""
    try:
        target_dir = _get_safe_path(subpath)
        if not target_dir.is_dir():
            return f"Error: '{subpath}' is not a valid directory."
        
        items = list(target_dir.iterdir())
        rel_to_vault = target_dir.relative_to(VAULT_DIR)
        display_path = "/vault" if str(rel_to_vault) == "." else f"/vault/{rel_to_vault}"
        
        if not items:
            return f"Contents of {display_path}:\n[Directory is empty]"
            
        result = f"Contents of {display_path}:\n"
        for item in items:
            item_type = "📁 [DIR]" if item.is_dir() else "📄 [FILE]"
            result += f"{item_type} {item.name}\n"
        return result
    except Exception as e:
        return f"Error listing files: {str(e)}"

def vault_read_file(filename):
    """Reads the content of a specific file."""
    try:
        target_file = _get_safe_path(filename)
        if not target_file.is_file():
            return f"Error: File '{filename}' does not exist."
            
        with open(target_file, "r", encoding="utf-8") as f:
            content = f.read()
        return content if content else "[File is empty]"
    except Exception as e:
        return f"Error reading file: {str(e)}"

def vault_write_file(filename, content):
    """Writes content to a specific file in the current directory."""
    try:
        target_file = _get_safe_path(filename)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(target_file, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully saved to {filename}."
    except Exception as e:
        return f"Error writing file: {str(e)}"

def vault_remove_file(filename):
    """Deletes a file after manual user confirmation."""
    try:
        target_file = _get_safe_path(filename)
        
        if not target_file.is_file():
            return f"Error: File '{filename}' does not exist."

        # Manual confirmation prompt in the terminal
        confirm = input(f"\n[CONFIRMATION] AI wants to DELETE file: {filename}\nProceed? (y/n): ").lower()
        
        if confirm == 'y':
            target_file.unlink()
            return f"Successfully deleted file: {filename}"
        else:
            return "Action cancelled by user."
            
    except Exception as e:
        return f"Error removing file: {str(e)}"

def vault_remove_directory(dirname):
    """Deletes a directory and all its contents after manual user confirmation."""
    try:
        target_dir = _get_safe_path(dirname)
        
        if not target_dir.is_dir():
            return f"Error: Directory '{dirname}' does not exist."

        # Safety check: Don't let it delete the root /vault/ folder itself
        if target_dir.resolve() == VAULT_DIR.resolve():
            return "Error: Access Denied. You cannot delete the root /vault/ folder."

        # Manual confirmation prompt in the terminal
        confirm = input(f"\n[WARNING] AI wants to DELETE ENTIRE DIRECTORY: {dirname}\nProceed? (y/n): ").lower()
        
        if confirm == 'y':
            shutil.rmtree(target_dir)
            return f"Successfully deleted directory and all contents: {dirname}"
        else:
            return "Action cancelled by user."
            
    except Exception as e:
        return f"Error removing directory: {str(e)}"

def vault_append_file(filename, content):
    """Adds content to the end of a file instead of overwriting it."""
    try:
        target_file = _get_safe_path(filename)
        with open(target_file, "a", encoding="utf-8") as f:
            f.write("\n" + content)
        return f"Successfully appended to {filename}."
    except Exception as e:
        return f"Error appending to file: {str(e)}"

def vault_move_item(source, destination):
    """Moves or renames a file/folder."""
    try:
        src_path = _get_safe_path(source)
        dest_path = _get_safe_path(destination)
        
        # Ensure destination directory exists
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        
        shutil.move(src_path, dest_path)
        return f"Successfully moved '{source}' to '{destination}'."
    except Exception as e:
        return f"Error moving item: {str(e)}"

def browser_wait(seconds):
    import time
    try:
        # Cap the wait time to 60 seconds so the model can't accidentally freeze itself forever
        wait_time = min(int(seconds), 60)
        time.sleep(wait_time)
        return f"Successfully waited for {wait_time} seconds."
    except Exception as e:
        return f"Error during wait: {str(e)}"

def browser_navigate(url): return browser_manager.execute("navigate", url=url)
def browser_get_view(): return browser_manager.execute("get_view")
def browser_click(element_id): return browser_manager.execute("click", element_id=element_id)
def browser_type(text, press_enter=False): return browser_manager.execute("type", text=text, press_enter=press_enter)
def browser_scroll(direction, amount=400): return browser_manager.execute("scroll", direction=direction, amount=amount)
def browser_hover_grid(grid_id): return browser_manager.execute("hover_grid", grid_id=grid_id)
def browser_click_hovered(): return browser_manager.execute("click_hovered")    
def browser_press_key(key): return browser_manager.execute("press_key", key=key)

def browser_list_tabs(): return browser_manager.execute("list_tabs")
def browser_open_tab(url=None): return browser_manager.execute("open_tab", url=url)
def browser_close_tab(tab_id): return browser_manager.execute("close_tab", tab_id=tab_id)
def browser_switch_tab(tab_id): return browser_manager.execute("switch_tab", tab_id=tab_id)

# ==========================================
# NAME -> FUNCTION MAPPING
# ==========================================

AVAILABLE_FUNCTIONS = {
    "current_datetime": current_datetime,
    "web_search": web_search,
    "scrape_url": scrape_url,
    "vault_change_directory": vault_change_directory,
    "vault_create_directory": vault_create_directory,
    "vault_list_files": vault_list_files,
    "vault_read_file": vault_read_file,
    "vault_write_file": vault_write_file,
    "vault_get_working_directory": vault_get_working_directory,
    "vault_remove_file": vault_remove_file,
    "vault_remove_directory": vault_remove_directory,
    "vault_append_file": vault_append_file,
    "vault_move_item": vault_move_item,
    "browser_wait": browser_wait,
    "browser_navigate": browser_navigate,
    "browser_get_view": browser_get_view,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_scroll": browser_scroll,
    "browser_hover_grid": browser_hover_grid,
    "browser_click_hovered": browser_click_hovered,
    "browser_press_key": browser_press_key,
    "browser_list_tabs": browser_list_tabs,
    "browser_open_tab": browser_open_tab,
    "browser_close_tab": browser_close_tab,
    "browser_switch_tab": browser_switch_tab,
}
def execute_tool(tool_name, arguments_json):
    """Finds the requested tool, runs it, and returns the result as a string."""
    try:
        args = json.loads(arguments_json)
        if tool_name in AVAILABLE_FUNCTIONS:
            return str(AVAILABLE_FUNCTIONS[tool_name](**args))
        else:
            return f"Error: Tool '{tool_name}' is not recognized."
    except Exception as e:
        return f"Error executing {tool_name}: {str(e)}"
# glbsmith

Turn a product photo and its dimensions into a true-to-size GLB. A local AI agent (Claude Code or Codex, using your own subscription) models the item part by part in Blender, compares renders with the photo, and fixes what is off.

Image-to-3D generators can stretch a model to the right outer size, but they often lose what is inside that box. An agent can see that a floor lamp has a big shade, a thin stem and a small base, and build each part on purpose.

| Works well | Works less well |
|---|---|
| Lamps, shelves, tables, chair frames, PC cases: clear parts, thin members, real gaps | Plush toys, wrinkled fabric, intricate carving, photo-exact patterns |

Sides that the photo does not show are still guesses.

## Quick start

Requirements: Linux, [uv](https://docs.astral.sh/uv/), Node.js 22+, [Blender](https://www.blender.org/download/) 5.2 LTS, and a logged-in `claude` or `codex` CLI.

```bash
uv sync
npm ci && npm run build
export BLENDER_BIN=/path/to/blender    # or unpack Blender so .tools/blender/blender exists
uv run python -m glbsmith.app          # http://127.0.0.1:8021
```

Add a photo, enter the width, depth and height, choose an agent and press **Create object**. Progress and renders appear as the agent works; the finished model appears in a 3D viewer next to the photo, with a download button.

To try the whole flow without using any quota, run the server with `GLBSMITH_FAKE_AGENT=1` and choose **Fake**. It builds two boxes.

You can also run a job from the command line:

```bash
uv run python -m glbsmith.run photo.png --width 35 --depth 35 --height 155 --agent claude
```

## What the GLB guarantees

The size you enter is the **box the item must fit in**, and the photo decides the proportions. `finalize` measures the model itself rather than trusting the agent:

- The model fits inside the box and touches it on at least one axis. Within 2% is corrected; beyond that the file is refused and the agent has to fix the model. The written file is read back and checked again.
- Axes that come out smaller than the box are shown as a warning on the page, because it usually means the size you entered doesn't match the photo.
- 1 unit = 1 metre, +Y up, front facing +Z, origin at the centre of the base.
- Each part is a separately named mesh with PBR materials.

Any app that follows these conventions can use the file. In [Room Setup](https://github.com/maromocooo/room-setup), use **Import GLB**.

## Checking against the photo

Before writing the file, the agent has to run `check_against_photo`, the reference-image check 3D artists do by hand. It finds the view that lines the model up with the photo, overlays them, and compares their widths band by band from top to bottom. A base drawn three times too wide shows up as a flagged band, which the agent then fixes. After three failed checks the file is written anyway, with a warning on the page.

Limits: it compares outlines from the one view in the photo, so it can't check depth seen end-on or details inside the outline, and it can't tell a box's front from its back. Those are left to the agent's eye and yours. Photos without a plain background can't be measured automatically; the page says so.

## How it works

```
Browser ──> FastAPI (localhost) ──> claude -p / codex exec
                                         │ MCP (stdio)
                                    glbsmith.mcp_server ──> blender --background
```

The agent gets seven tools and nothing else: `get_brief`, `run_python`, `render_views`, `measure`, `check_against_photo`, `bake_materials` and `finalize`. See [prompts/modeler.md](prompts/modeler.md) for its instructions.

## Security and privacy

`run_python` runs code that an AI wrote, and text inside a photo could try to steer that AI. So:

- The Blender process locks itself down with [Landlock](https://docs.kernel.org/userspace-api/landlock.html) before running any of that code. It can read Blender and system libraries, can write only to the job folder, and has no TCP access. It receives none of your environment variables. An audit hook also refuses UDP sockets and new processes from Python, but that hook is a speed bump, not a wall: determined code using `ctypes` could still send UDP.
- If Landlock is not available, the app says so and asks for your explicit consent before running.
- The agents run without shell or file tools, and without your settings, hooks or other MCP servers.
- The server binds to localhost and refuses other sites and hostnames. It has no authentication, so do not expose it to a network.
- The photo is sent to the service of the agent you choose. EXIF data such as GPS position is removed first.
- `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are removed from the agent's environment, so a job cannot silently switch to pay-per-use billing.

## Images and licences

Use photos you have the right to use. A model made from someone else's product photo is best kept to personal use, such as planning your own room.

glbsmith is [MIT](LICENSE) licensed. Blender is GPL and is not included.

## Tests

```bash
uv run pytest                # real Blender and the fake agent; uses no quota
GLBSMITH_FAKE_AGENT=1 GLBSMITH_JOBS=$(mktemp -d) uv run python -m glbsmith.app &
npm run test:ui
```

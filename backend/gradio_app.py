"""
gradio_app.py
UPFH Virtual Front Desk – baby‑blue embedded widget (Gradio 4.x)
-----------------------------------------------------------------
• build_widget()  → imported by backend.main
• python gradio_app.py  → run widget by itself on :7860
"""

import os, functools
import gradio as gr
from bot.chatbot import chat                # ← your existing LLM function

# ── 🎨  BRAND & TEXT SETTINGS ───────────────────────────────────────────────
BRAND_BLUE   = "#8EC9FF"   # header & launcher colour
PILL_BG      = "#EAF6FF"
PILL_TEXT    = "#2176D2"
BUBBLE_USER  = "#F0F5FF"
BUBBLE_BOT   = "#FFFFFF"
MAX_WIDTH_PX = 380

WELCOME_MSG = (
    "👋 **Hi! I’m the Midtown Community Health Center Virtual Front Desk.**\n\n"
    "_How can I help today?_"
)

# ── shared callback ─────────────────────────────────────────────────────────
def respond(message: str, history: list | None):
    answer = chat(message, history)
    history.append((message, answer))
    return history, ""                 # clear textbox

# ── builder (exported) ──────────────────────────────────────────────────────
def build_widget() -> gr.Blocks:
    seed_history = [[None, WELCOME_MSG]]

    with gr.Blocks(
        theme=gr.themes.Soft(primary_hue="blue").set(
            body_background_fill="#FFFFFF",
            body_text_color="#1E1F26",
        ),
        css=f"""
        /* widget frame */
        #widget{{position:fixed; bottom:90px; right:24px;
                 width:{MAX_WIDTH_PX}px; max-height:70vh;
                 display:none; flex-direction:column;
                 border-radius:12px; overflow:hidden;
                 box-shadow:0 8px 24px rgba(0,0,0,.15);}}

/* header */
#header{{background:{BRAND_BLUE}; color:#fff;
        padding:12px 16px; display:flex; align-items:center;}}
#header h4{{margin:0; font-size:16px; font-weight:600;}}

/* quick‑action pills */
.pill button{{background:{PILL_BG}!important; color:{PILL_TEXT};
              border:none; padding:6px 12px;
              font-size:14px; border-radius:20px;}}

/* chat bubbles */
.message.user{{background:{BUBBLE_USER};}}
.message.ai  {{background:{BUBBLE_BOT};}}

/* floating launcher */
#launcher{{position:fixed; bottom:24px; right:24px; width:56px; height:56px;
           border-radius:50%; background:{BRAND_BLUE}; color:#fff;
           font-size:26px; display:flex; align-items:center; justify-content:center;
           box-shadow:0 4px 12px rgba(0,0,0,.2); cursor:pointer;}}

/* sticky input bar inside widget */
#input_bar{{position:sticky; bottom:0; background:#fff;
            padding:6px 8px 8px;}}
        """
    ) as demo:

        # ── collapsible panel ────────────────────────────────
        with gr.Column(elem_id="widget") as panel:

            # header
            with gr.Row(elem_id="header"):
                gr.Markdown("<h4>UPFH Clinic</h4>", elem_id="brand_title")

            # quick‑reply pills
            with gr.Row():
                btn_appt = gr.Button("Appointments",   elem_classes=["pill"])
                btn_cost = gr.Button("Estimated Costs", elem_classes=["pill"])
                btn_gen  = gr.Button("General Questions", elem_classes=["pill"])

            # chat area – takes all remaining vertical space
            chatbot = gr.Chatbot(
                value=seed_history,
                height=360,
                bubble_full_width=False,
                layout="bubble",
                container=False,   # let flexbox stretch it
            )

            # sticky input bar
            with gr.Column(elem_id="input_bar"):
                textbox = gr.Textbox(
                    placeholder="Type here & press Enter…",
                    lines=1,
                    show_label=False,
                    container=False,
                )
                submit_btn = gr.Button("Submit", variant="primary", size="sm")

        # floating launcher
        launcher = gr.HTML("<div id='launcher'>💬</div>")

        # wiring
        textbox.submit(respond, [textbox, chatbot], [chatbot, textbox])
        submit_btn.click(respond, [textbox, chatbot], [chatbot, textbox])

        btn_appt.click(
            functools.partial(respond, "I’d like to book or change an appointment"),
            [chatbot], [chatbot, textbox]
        )
        btn_cost.click(
            functools.partial(respond, "Can you estimate my costs?"),
            [chatbot], [chatbot, textbox]
        )
        btn_gen.click(
            functools.partial(respond, "I have a general question"),
            [chatbot], [chatbot, textbox]
        )

        # JS toggle
        demo.load(None, None, _js="""
            () => {
              const widget   = document.getElementById('widget');
              const launcher = document.getElementById('launcher');
              launcher.onclick = () => {
                  widget.style.display =
                      widget.style.display === 'none' ? 'flex' : 'none';
              };
            }
        """)

    return demo

# ── stand‑alone launcher (optional) ───────────────────────────
if __name__ == "__main__":
    build_widget().launch(
        server_name="0.0.0.0",
        server_port=int(os.getenv("PORT", 7860)),
        share=False,
    )

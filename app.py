"""HABIX AI local development entry point."""
from habix import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        debug=app.config["DEBUG"],
        host="127.0.0.1",
        port=5000,
        use_reloader=False,
        threaded=True,
    )

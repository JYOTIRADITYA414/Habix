"""Desktop-packaged backend entry point for PyInstaller."""
import os

from habix import create_app


def main():
    app = create_app()
    host = os.environ.get("HABIX_HOST", "127.0.0.1")
    port = int(os.environ.get("HABIX_PORT", "5000"))
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()

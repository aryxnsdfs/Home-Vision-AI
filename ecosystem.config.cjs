module.exports = {
  apps: [
    {
      name: "home-backend",
      script: "/var/www/sanky/server.py",
      interpreter: "/var/www/sanky/venv/bin/python",
      cwd: "/var/www/sanky",
      restart_delay: 5000,
      max_restarts: 10,
    },
    {
      name: "home-celery",
      script: "/var/www/sanky/venv/bin/celery",
      args: "-A celery_worker worker --loglevel=info --pool=solo",
      cwd: "/var/www/sanky",
      restart_delay: 5000,
      max_restarts: 10,
    },
    {
      name: "home-frontend",
      script: "npm",
      args: "run dev",
      cwd: "/var/www/sanky",
      restart_delay: 5000,
      max_restarts: 10,
    }
  ]
}

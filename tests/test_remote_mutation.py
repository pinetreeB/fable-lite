from __future__ import annotations

from core.shell_command import is_remote_only_mutation_command


def test_direct_ssh_and_scp_upload_are_remote_only_mutations() -> None:
    commands = (
        'ssh deploy@example.com "sudo systemctl restart app"',
        "ssh -p 2222 deploy@example.com uptime",
        "scp artifact.tar deploy@example.com:/srv/app/",
        "scp -P 2222 -r dist deploy@example.com:/srv/app/",
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is True, command


def test_remote_commands_with_identity_and_attached_port_options_stay_remote_only() -> None:
    commands = (
        'ssh -i deploy-key deploy@example.com "touch remote-marker"',
        'ssh -J bastion deploy@example.com "touch remote-marker"',
        "scp -i deploy-key artifact.tar deploy@example.com:/srv/app/",
        "scp -J bastion artifact.tar deploy@example.com:/srv/app/",
        "scp -o BatchMode=yes artifact.tar deploy@example.com:/srv/app/",
        'ssh -p2222 deploy@example.com "touch remote-marker"',
        "scp -P2222 artifact.tar deploy@example.com:/srv/app/",
    )

    # Given/When/Then: safe option values are consumed before remote operands are classified.
    assert tuple(is_remote_only_mutation_command(command) for command in commands) == (
        True,
        True,
        True,
        True,
        True,
        True,
        True,
    )


def test_remote_commands_with_combined_safe_flags_stay_remote_only() -> None:
    commands = (
        'ssh -qT deploy@example.com "touch remote-marker"',
        "scp -pr dist deploy@example.com:/srv/app/",
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is True, command


def test_supported_read_only_openssh_options_do_not_hide_remote_mutations() -> None:
    commands = (
        'ssh -ACfKksXxY deploy@example.com "touch remote-marker"',
        'ssh -B Ethernet -b 127.0.0.1 -c aes128-ctr deploy@example.com "touch remote-marker"',
        'ssh -e none -m hmac-sha2-256 deploy@example.com "touch remote-marker"',
        'ssh -D 1080 -L 8080:localhost:80 -R 9090:localhost:90 deploy@example.com "touch remote-marker"',
        "scp -3ABCOqT artifact.tar deploy@example.com:/srv/app/",
        "scp -c aes128-ctr -l 1000 -X nrequests=64 artifact.tar deploy@example.com:/srv/app/",
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is True, command


def test_read_only_ssh_config_options_do_not_hide_remote_mutations() -> None:
    commands = (
        'ssh -o Compression=yes -o Ciphers=aes128-ctr -o Port=2222 -o User=deploy host "touch remote-marker"',
        "scp -o Compression=yes -o Ciphers=aes128-ctr -o Port=2222 -o User=deploy artifact.tar host:/srv/app/",
        'ssh -o StrictHostKeyChecking=yes host "touch remote-marker"',
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is True, command


def test_remote_commands_with_local_or_mixed_effects_are_not_remote_only() -> None:
    commands = (
        "ssh deploy@example.com uptime > local.log",
        "ssh deploy@example.com uptime | tee local.log",
        "scp deploy@example.com:/tmp/a ./a",
        "scp local.txt deploy@example.com:/tmp/a && rm local.txt",
        "ssh deploy@example.com $(cat local.txt)",
        "ssh deploy@example.com `cat local.txt`",
        "ssh deploy@example.com uptime\nprintf done",
        "ssh -E local.log deploy@example.com uptime",
        'ssh -I ./provider.dll deploy@example.com "touch remote-marker"',
        'ssh -L ./local.sock:remote.sock deploy@example.com "touch remote-marker"',
        "ssh -S local.sock deploy@example.com uptime",
        "ssh -M deploy@example.com uptime",
        "ssh -O check deploy@example.com",
        'ssh -P release deploy@example.com "touch remote-marker"',
        "ssh -G deploy@example.com",
        "ssh -N deploy@example.com",
        "ssh -Q cipher",
        "ssh -V",
        "ssh -W target.example.com:22 deploy@example.com",
        'ssh -y deploy@example.com "touch remote-marker"',
        "ssh -o LocalCommand=touch-local deploy@example.com uptime",
        "ssh -o ProxyCommand=local-proxy deploy@example.com uptime",
        "ssh -o ControlPath=local.sock deploy@example.com uptime",
        'ssh -o StrictHostKeyChecking=accept-new host "touch remote-marker"',
        'ssh -o StrictHostKeyChecking=ask host "touch remote-marker"',
        'ssh -o StrictHostKeyChecking=no host "touch remote-marker"',
        'ssh -o StrictHostKeyChecking=off host "touch remote-marker"',
        (
            "ssh -o StrictHostKeyChecking=accept-new "
            "-o UserKnownHostsFile=./local-known-hosts deploy@example.com uptime"
        ),
        "scp -D local-sftp artifact.tar deploy@example.com:/srv/app/",
        "scp -F ssh-config artifact.tar deploy@example.com:/srv/app/",
        "scp -S ssh-wrapper artifact.tar deploy@example.com:/srv/app/",
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is False, command

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
        "scp -i deploy-key artifact.tar deploy@example.com:/srv/app/",
        'ssh -p2222 deploy@example.com "touch remote-marker"',
        "scp -P2222 artifact.tar deploy@example.com:/srv/app/",
    )

    # Given/When/Then: safe option values are consumed before remote operands are classified.
    assert tuple(is_remote_only_mutation_command(command) for command in commands) == (
        True,
        True,
        True,
        True,
    )


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
        "ssh -S local.sock deploy@example.com uptime",
        "ssh -M deploy@example.com uptime",
        "ssh -O check deploy@example.com",
        "ssh -o LocalCommand=touch-local deploy@example.com uptime",
        "ssh -o ProxyCommand=local-proxy deploy@example.com uptime",
        "ssh -o ControlPath=local.sock deploy@example.com uptime",
        (
            "ssh -o StrictHostKeyChecking=accept-new "
            "-o UserKnownHostsFile=./local-known-hosts deploy@example.com uptime"
        ),
    )

    for command in commands:
        assert is_remote_only_mutation_command(command) is False, command

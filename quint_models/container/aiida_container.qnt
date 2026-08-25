// Lifecycle model for run.py and containerfiles/aiida.def.
//
// Covers image patch validation, the single-data-directory lock, dependency
// readiness, the session command lifetime in both of run.py's modes, attached
// shells, and supervised shutdown.  Abstracts host-side details, s6 internals,
// ports, time, and application traffic.
// Source map: this module corresponds to both files named above. Update and
// rerun it whenever either source file changes.
//
// A session is `run.py` in either mode: foreground (`./run.py [COMMAND]`),
// whose command exits on its own, or detached (`./run.py up`), whose command
// is `sleep infinity` and only ends when `./run.py down` signals it.  An
// attached shell (`./run.py attach`) is a *second* container started with
// `apptainer exec`: it runs no s6 and starts no service, and it does not take
// the data-directory lock -- which is why several may coexist with one
// session without breaking `servicesRequireDataLock`.
//
// Not modeled: `./run.py attach` and `./run.py down` refusing to run when no
// session holds the lock.  Both are host-side diagnostics that leave every
// modeled variable untouched; the guards on `attachShell` and `requestStop`
// encode the same precondition.
module aiida_container {
  type ImagePhase = Absent | Building | Valid | Invalid
  type SessionMode = NoSession | Foreground | Detached
  type SessionPhase =
    | Idle
    | Validating
    | AcquiringLock
    | Launching
    | StartingServices
    | RunningCommand
    | Stopping
    | Finished
    | Failed
  type ServicePhase = Down | Starting | Ready
  type PostgresIdentity = UnknownPostgres | BundledPostgres | ForeignPostgres
  type FailureReason =
    | NoFailure
    | MissingImage
    | PatchRejected
    | MissingApptainer
    | DataDirectoryBusy
    | PostgresUnavailable
    | ForeignPostgresDetected
    | RabbitUnavailable

  // An attached shell holds no lock and no service, so the only thing the
  // model needs from it is how many are around; two is enough to exercise
  // "more than one" without inflating the state space.
  pure val maxAttachedShells: int = 2

  type ContainerState = {
    image: ImagePhase,
    session: SessionPhase,
    mode: SessionMode,
    attachedShells: int,
    lockHeld: bool,
    postgres: ServicePhase,
    postgresIdentity: PostgresIdentity,
    rabbitmq: ServicePhase,
    aiidaDaemon: ServicePhase,
    profileReady: bool,
    commandExited: bool,
    stopRequested: bool,
    failure: FailureReason,
  }

  var state: ContainerState

  pure val initialState: ContainerState = {
    image: Absent,
    session: Idle,
    mode: NoSession,
    attachedShells: 0,
    lockHeld: false,
    postgres: Down,
    postgresIdentity: UnknownPostgres,
    rabbitmq: Down,
    aiidaDaemon: Down,
    profileReady: false,
    commandExited: false,
    stopRequested: false,
    failure: NoFailure,
  }

  pure def canBeginBuild(s: ContainerState): bool =
    s.image == Absent and s.session == Idle

  pure def applyBeginBuild(s: ContainerState): ContainerState =
    { ...s, image: Building }

  pure def canFinishBuild(s: ContainerState): bool =
    s.image == Building and s.session == Idle

  pure def applyBuildSuccess(s: ContainerState): ContainerState =
    { ...s, image: Valid }

  pure def applyBuildRejected(s: ContainerState): ContainerState =
    { ...s, image: Invalid, session: Failed, failure: PatchRejected }

  // Both modes take the same path: run.py checks the runtime before it
  // touches the data directory, whether it will stay in the foreground or
  // detach.
  pure def canStartSession(s: ContainerState): bool =
    s.session == Idle

  pure def applyStartSession(
    s: ContainerState,
    mode: SessionMode,
  ): ContainerState =
    { ...s, session: Validating, mode: mode }

  // run.py's apptainer() checks the binary and the image together, so both
  // rejections leave from the same phase.  The flock binary is no longer one
  // of them: the lock is taken through fcntl, which is in the standard
  // library.
  pure def canValidate(s: ContainerState): bool =
    s.session == Validating

  pure def applyMissingApptainer(s: ContainerState): ContainerState =
    { ...s, session: Failed, failure: MissingApptainer }

  pure def canRejectMissingImage(s: ContainerState): bool =
    s.session == Validating and s.image != Valid

  pure def applyMissingImage(s: ContainerState): ContainerState =
    { ...s, session: Failed, failure: MissingImage }

  pure def canFinishValidation(s: ContainerState): bool =
    s.session == Validating and s.image == Valid

  pure def applyValidated(s: ContainerState): ContainerState =
    { ...s, session: AcquiringLock }

  pure def canAcquireLock(s: ContainerState): bool =
    s.session == AcquiringLock

  pure def applyLockAcquired(s: ContainerState): ContainerState =
    { ...s, session: Launching, lockHeld: true }

  pure def applyLockBusy(s: ContainerState): ContainerState =
    { ...s, session: Failed, failure: DataDirectoryBusy }

  pure def canLaunch(s: ContainerState): bool =
    s.session == Launching and s.lockHeld

  pure def applyLaunch(s: ContainerState): ContainerState =
    {
      ...s,
      session: StartingServices,
      postgres: Starting,
      rabbitmq: Starting,
    }

  pure def canProbePostgres(s: ContainerState): bool =
    s.session == StartingServices and s.postgres == Starting

  pure def applyBundledPostgresReady(s: ContainerState): ContainerState =
    { ...s, postgres: Ready, postgresIdentity: BundledPostgres }

  pure def applyPostgresFailure(
    s: ContainerState,
    identity: PostgresIdentity,
    reason: FailureReason,
  ): ContainerState =
    {
      ...s,
      session: Stopping,
      postgresIdentity: identity,
      failure: reason,
    }

  pure def canProbeRabbit(s: ContainerState): bool =
    s.session == StartingServices and s.rabbitmq == Starting

  pure def applyRabbitReady(s: ContainerState): ContainerState =
    { ...s, rabbitmq: Ready }

  pure def applyRabbitFailure(s: ContainerState): ContainerState =
    { ...s, session: Stopping, failure: RabbitUnavailable }

  pure def canStartAiida(s: ContainerState): bool =
    and {
      s.session == StartingServices,
      s.postgres == Ready,
      s.postgresIdentity == BundledPostgres,
      s.rabbitmq == Ready,
      s.aiidaDaemon == Down,
    }

  pure def applyStartAiida(s: ContainerState): ContainerState =
    { ...s, aiidaDaemon: Starting }

  pure def canFinishAiidaStart(s: ContainerState): bool =
    and {
      s.session == StartingServices,
      s.postgres == Ready,
      s.postgresIdentity == BundledPostgres,
      s.rabbitmq == Ready,
      s.aiidaDaemon == Starting,
    }

  pure def applyAiidaReady(s: ContainerState): ContainerState =
    {
      ...s,
      session: RunningCommand,
      aiidaDaemon: Ready,
      profileReady: true,
    }

  // `apptainer exec` needs nothing but a live session holding the lock -- not
  // even ready services, which is why attaching too early is reachable here.
  pure def canAttachShell(s: ContainerState): bool =
    s.lockHeld and s.attachedShells < maxAttachedShells

  pure def applyAttachShell(s: ContainerState): ContainerState =
    { ...s, attachedShells: s.attachedShells + 1 }

  // An attached shell is an independent container; it may also be left behind
  // when the session it was talking to goes away.
  pure def canDetachShell(s: ContainerState): bool =
    s.attachedShells > 0

  pure def applyDetachShell(s: ContainerState): ContainerState =
    { ...s, attachedShells: s.attachedShells - 1 }

  // Only a foreground session has a command that returns.  `up` runs
  // `sleep infinity`, so a detached session ends only on a stop signal.
  pure def canExitCommand(s: ContainerState): bool =
    and {
      s.session == RunningCommand,
      s.mode == Foreground,
      s.aiidaDaemon == Ready,
    }

  pure def applyCommandExit(s: ContainerState): ContainerState =
    { ...s, session: Stopping, commandExited: true }

  // `./run.py down` -- SIGTERM to the session, which s6 turns into an ordered
  // shutdown.  The same signal reaches a foreground session from the
  // terminal, so it is not restricted to the detached mode.
  pure def canRequestStop(s: ContainerState): bool =
    s.session == RunningCommand

  pure def applyRequestStop(s: ContainerState): ContainerState =
    { ...s, session: Stopping, stopRequested: true }

  pure def canStopAiida(s: ContainerState): bool =
    s.session == Stopping and s.aiidaDaemon != Down

  pure def applyStopAiida(s: ContainerState): ContainerState =
    { ...s, aiidaDaemon: Down }

  pure def canStopRabbit(s: ContainerState): bool =
    and {
      s.session == Stopping,
      s.aiidaDaemon == Down,
      s.rabbitmq != Down,
    }

  pure def applyStopRabbit(s: ContainerState): ContainerState =
    { ...s, rabbitmq: Down }

  pure def canStopPostgres(s: ContainerState): bool =
    and {
      s.session == Stopping,
      s.aiidaDaemon == Down,
      s.postgres != Down,
    }

  pure def applyStopPostgres(s: ContainerState): ContainerState =
    { ...s, postgres: Down }

  pure def canFinishShutdown(s: ContainerState): bool =
    and {
      s.session == Stopping,
      s.lockHeld,
      s.aiidaDaemon == Down,
      s.rabbitmq == Down,
      s.postgres == Down,
    }

  pure def applyFinishShutdown(s: ContainerState): ContainerState =
    { ...s, session: Finished, lockHeld: false }

  action init: bool =
    state' = initialState

  action beginBuild: bool = all {
    canBeginBuild(state),
    state' = applyBeginBuild(state),
  }

  action buildSuccess: bool = all {
    canFinishBuild(state),
    state' = applyBuildSuccess(state),
  }

  action buildRejected: bool = all {
    canFinishBuild(state),
    state' = applyBuildRejected(state),
  }

  action startForegroundSession: bool = all {
    canStartSession(state),
    state' = applyStartSession(state, Foreground),
  }

  action startDetachedSession: bool = all {
    canStartSession(state),
    state' = applyStartSession(state, Detached),
  }

  action rejectMissingApptainer: bool = all {
    canValidate(state),
    state' = applyMissingApptainer(state),
  }

  action rejectMissingImage: bool = all {
    canRejectMissingImage(state),
    state' = applyMissingImage(state),
  }

  action validationPassed: bool = all {
    canFinishValidation(state),
    state' = applyValidated(state),
  }

  action acquireDataLock: bool = all {
    canAcquireLock(state),
    state' = applyLockAcquired(state),
  }

  action rejectBusyDataDirectory: bool = all {
    canAcquireLock(state),
    state' = applyLockBusy(state),
  }

  action launchContainer: bool = all {
    canLaunch(state),
    state' = applyLaunch(state),
  }

  action bundledPostgresReady: bool = all {
    canProbePostgres(state),
    state' = applyBundledPostgresReady(state),
  }

  action rejectForeignPostgres: bool = all {
    canProbePostgres(state),
    state' = applyPostgresFailure(
      state,
      ForeignPostgres,
      ForeignPostgresDetected,
    ),
  }

  action postgresUnavailable: bool = all {
    canProbePostgres(state),
    state' = applyPostgresFailure(
      state,
      UnknownPostgres,
      PostgresUnavailable,
    ),
  }

  action rabbitReady: bool = all {
    canProbeRabbit(state),
    state' = applyRabbitReady(state),
  }

  action rabbitUnavailable: bool = all {
    canProbeRabbit(state),
    state' = applyRabbitFailure(state),
  }

  action startAiida: bool = all {
    canStartAiida(state),
    state' = applyStartAiida(state),
  }

  action aiidaReady: bool = all {
    canFinishAiidaStart(state),
    state' = applyAiidaReady(state),
  }

  action attachShell: bool = all {
    canAttachShell(state),
    state' = applyAttachShell(state),
  }

  action detachShell: bool = all {
    canDetachShell(state),
    state' = applyDetachShell(state),
  }

  action commandExit: bool = all {
    canExitCommand(state),
    state' = applyCommandExit(state),
  }

  action requestStop: bool = all {
    canRequestStop(state),
    state' = applyRequestStop(state),
  }

  action stopAiida: bool = all {
    canStopAiida(state),
    state' = applyStopAiida(state),
  }

  action stopRabbit: bool = all {
    canStopRabbit(state),
    state' = applyStopRabbit(state),
  }

  action stopPostgres: bool = all {
    canStopPostgres(state),
    state' = applyStopPostgres(state),
  }

  action finishShutdown: bool = all {
    canFinishShutdown(state),
    state' = applyFinishShutdown(state),
  }

  action step: bool = any {
    beginBuild,
    buildSuccess,
    buildRejected,
    startForegroundSession,
    startDetachedSession,
    rejectMissingApptainer,
    rejectMissingImage,
    validationPassed,
    acquireDataLock,
    rejectBusyDataDirectory,
    launchContainer,
    bundledPostgresReady,
    rejectForeignPostgres,
    postgresUnavailable,
    rabbitReady,
    rabbitUnavailable,
    startAiida,
    aiidaReady,
    attachShell,
    detachShell,
    commandExit,
    requestStop,
    stopAiida,
    stopRabbit,
    stopPostgres,
    finishShutdown,
  }

  val aiidaRequiresReadyDependencies: bool =
    state.aiidaDaemon != Down implies and {
      state.postgres == Ready,
      state.postgresIdentity == BundledPostgres,
      state.rabbitmq == Ready,
    }

  val profileUsesBundledServices: bool =
    state.profileReady implies and {
      state.postgresIdentity == BundledPostgres,
      state.rabbitmq == Ready or state.session == Stopping or state.session == Finished,
    }

  val servicesRequireDataLock: bool =
    or {
      and {
        state.postgres == Down,
        state.rabbitmq == Down,
        state.aiidaDaemon == Down,
      },
      state.lockHeld,
    }

  val foreignPostgresIsRefused: bool =
    state.postgresIdentity == ForeignPostgres implies and {
      state.aiidaDaemon == Down,
      not(state.profileReady),
      state.failure == ForeignPostgresDetected,
    }

  // Attached shells start no service, so they must never be the reason the
  // lock is held -- `servicesRequireDataLock` above is what would break if
  // this model ever gave `attachShell` a service of its own.
  val attachedShellsBelongToASession: bool =
    state.attachedShells > 0 implies state.mode != NoSession

  // `up`'s command is `sleep infinity`; only a signal ends a detached session.
  val detachedCommandNeverReturns: bool =
    state.commandExited implies state.mode == Foreground

  // Every way into shutdown is accounted for: the command returned, someone
  // asked for it, or a service failed to come up.
  val shutdownHasACause: bool =
    state.session == Stopping implies or {
      state.commandExited,
      state.stopRequested,
      state.failure != NoFailure,
    }

  val finishedSessionIsClean: bool =
    state.session == Finished implies and {
      not(state.lockHeld),
      state.postgres == Down,
      state.rabbitmq == Down,
      state.aiidaDaemon == Down,
    }
}

import dataclasses
import datetime
import dateutil.parser
import enum
import functools
import itertools
import os
import typing

import dacite
import yaml

own_dir = os.path.abspath(os.path.dirname(__file__))
repo_root = os.path.abspath(os.path.join(own_dir, os.path.pardir, os.path.pardir))


class FeatureType(enum.Enum):
    '''
    gardenlinux feature types as used in `features/*/info.yaml`

    Each gardenlinux flavour MUST specify exactly one platform and MAY
    specify an arbitrary amount of modifiers.
    '''
    PLATFORM = 'platform'
    MODIFIER = 'modifier'


@dataclasses.dataclass(frozen=True)
class FeatureDescriptor:
    '''
    A gardenlinux feature descriptor (parsed from $repo_root/features/*/info.yaml)
    '''
    type: FeatureType
    name: str
    description: str = 'no description available'


class Architecture(enum.Enum):
    '''
    gardenlinux' target architectures, following Debian's naming
    '''
    AMD64 = 'amd64'


Platform = str # see `features/*/info.yaml` / platforms() for allowed values
Modifier = str # see `features/*/info.yaml` / modifiers() for allowed values


@dataclasses.dataclass(frozen=True)
class GardenlinuxFlavour:
    '''
    A specific flavour of gardenlinux.
    '''
    architecture: Architecture
    platform: str
    modifiers: typing.Tuple[Modifier]

    def canonical_name_prefix(self):
        a = self.architecture.value
        fname_prefix = self.filename_prefix()

        return f'{a}/{fname_prefix}'

    def filename_prefix(self):
        p = self.platform
        m = '_'.join(sorted([m for m in self.modifiers]))

        return f'{p}-{m}'

    def release_files(self, version: str):
        suffices = (
            'InRelease',
            'Release',
            'rootf.tar.xz',
            'rootfs.raw',
            'manifest',
        )

        prefix = self.canonical_name_prefix()

        for s in suffices:
            yield f'{prefix}-{version}-{s}'

    def __post_init__(self):
        # validate platform and modifiers
        platform_names = {platform.name for platform in platforms()}
        if not self.platform in platform_names:
            raise ValueError(
                f'unknown platform: {self.platform}. known: {platform_names}'
            )

        modifier_names = {modifier.name for modifier in modifiers()}
        unknown_mods = set(self.modifiers) - modifier_names
        if unknown_mods:
            raise ValueError(
                f'unknown modifiers: {unknown_mods}. known: {modifier_names}'
            )


@dataclasses.dataclass(frozen=True)
class GardenlinuxFlavourCombination:
    '''
    A declaration of a set of gardenlinux flavours. Deserialised from `build.yaml`.

    We intend to build a two-digit number of gardenlinux flavours (combinations
    of different architectures, platforms, and modifiers). To avoid tedious and redundant
    manual configuration, flavourset combinations are declared. Subsequently, the
    cross product of said combinations are generated.
    '''
    architectures: typing.Tuple[Architecture]
    platforms: typing.Tuple[Platform]
    modifiers: typing.Tuple[typing.Tuple[Modifier]]


@dataclasses.dataclass(frozen=True)
class GardenlinuxFlavourSet:
    '''
    A set of gardenlinux flavours
    '''
    name: str
    flavour_combinations: typing.Tuple[GardenlinuxFlavourCombination]

    def flavours(self):
        for comb in self.flavour_combinations:
            for arch, platf, mods in itertools.product(
                comb.architectures,
                comb.platforms,
                comb.modifiers,
            ):
                yield GardenlinuxFlavour(
                    architecture=arch,
                    platform=platf,
                    modifiers=mods,
                )


@dataclasses.dataclass(frozen=True)
class ReleaseFile:
    '''
    A single build result file that was (or will be) uploaded to build result persistency store
    (S3).
    '''
    rel_path: str
    name: str
    suffix: str


@dataclasses.dataclass(frozen=True)
class ReleaseManifest:
    '''
    metadata for a gardenlinux release variant that has been published to a persistency
    store, such as an S3 bucket.
    '''
    build_committish: str
    build_timestamp: str
    gardenlinux_epoch: int
    architecture: Architecture
    platform: Platform
    modifiers: typing.Tuple[Modifier]
    paths: typing.Tuple[ReleaseFile]

    def path_by_suffix(self, suffix: str):
        for path in self.paths:
            if path.suffix == suffix:
                return path
        else:
            raise ValueError(f'no path with {suffix=}')

    def flavour(self) -> GardenlinuxFlavour:
        return GardenlinuxFlavour(
            architecture=self.architecture,
            platform=self.platform,
            modifiers=self.modifiers,
        )

    # treat as "static final"
    manifest_key_prefix = 'meta'


class PipelineFlavour(enum.Enum):
    SNAPSHOT = 'snapshot'
    RELEASE = 'release'


@dataclasses.dataclass(frozen=True)
class BuildCfg:
    aws_cfg_name: str
    s3_bucket_name: str
    manifest_key_root_prefix: str='meta'

    def manifest_key_prefix(self, name: str):
        return os.path.join(self.manifest_key_root_prefix, name)


@dataclasses.dataclass(frozen=True)
class CicdCfg:
    name: str
    build: BuildCfg


def gardenlinux_epoch(date:typing.Union[str, datetime.datetime]=None):
    '''
    calculate the gardenlinux epoch for the given date (the amount of days since 2020-04-01)
    @param date: date (defaults to today); if str, must be compliant to iso-8601
    '''
    if date is None:
        date = datetime.datetime.today()
    elif isinstance(date, str):
        date = dateutil.parser.isoparse(date)

    if not isinstance(date, datetime.datetime):
        raise ValueError(date)

    epoch_date = datetime.datetime.fromisoformat('2020-04-01')

    gardenlinux_epoch = (date - epoch_date).days

    if gardenlinux_epoch < 0:
        raise ValueError() # must not be older than gardenlinux' inception
    return gardenlinux_epoch


def _enumerate_feature_files(features_dir=os.path.join(repo_root, 'features')):
    for root, _, files in os.walk(features_dir):
        for name in files:
            if not name == 'info.yaml':
                continue
            yield os.path.join(root, name)


def _deserialise_feature(feature_file):
    with open(feature_file) as f:
        parsed = yaml.safe_load(f)
    # hack: inject name from pardir
    pardir = os.path.basename(os.path.dirname(feature_file))
    parsed['name'] = pardir

    return dacite.from_dict(
        data_class=FeatureDescriptor,
        data=parsed,
        config=dacite.Config(
            cast=[
                FeatureType,
            ],
        ),
    )


@functools.lru_cache
def features():
    return {
        _deserialise_feature(feature_file)
        for feature_file in _enumerate_feature_files()
    }


def platforms():
    return {
        feature for feature in features() if feature.type is FeatureType.PLATFORM
    }

def modifiers():
    return {
        feature for feature in features() if feature.type is FeatureType.MODIFIER
    }
